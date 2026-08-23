"""The two credit brakes: the stalled-job watchdog and the daily run budget.

Both exist because of things that were watched happening. Bright Data accepted an
Instamart trigger in under a second, reported the job as running, and then never
allocated a worker to it: `navigations` sat at 0 for as long as anyone looked,
three separate times in one day, while the identical template delivered rows in
36s on the very next trigger. And when the app gave up on a job, the job did not:
one was observed still running 319s later, billing the whole time, until it was
canceled by hand.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from server import runs as runs_module
from server.app import create_app
from server.bd_client import JobLog

from conftest import FIXTURES, wait_for_done


ZEPTO_ROWS = json.loads(
    (FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8")
)

# `(status, navigations, lines)` repeated forever. This is the failure: accepted,
# running, and nothing behind it.
STALLED = [("running", 0, 0)]


class FakeBD:
    """A scriptable stand-in for the Bright Data client, one script per job.

    Scripts are consumed in trigger order, so a test can say "the first job
    stalls, the second one works" — which is exactly what the retrigger is for.
    A script's last entry repeats for as long as the job is polled, so a job that
    never starts is a one-entry script rather than a long one.
    """

    mode = "live"

    def __init__(self, scripts: list[list[tuple[str, int | None, int | None]]]) -> None:
        self.scripts = scripts
        self.triggers: list[str] = []
        self.cancels: list[str] = []
        self._script_for: dict[str, list[tuple[str, int | None, int | None]]] = {}
        self._polls: dict[str, int] = {}

    async def trigger(self, collector_id: str, inputs: list[dict[str, Any]], version: str) -> str:
        job_id = f"j_fake_{len(self.triggers) + 1}"
        self._script_for[job_id] = self.scripts[min(len(self.triggers), len(self.scripts) - 1)]
        self._polls[job_id] = 0
        self.triggers.append(job_id)
        return job_id

    async def job_log(self, job_id: str) -> JobLog:
        script = self._script_for[job_id]
        status, navigations, lines = script[min(self._polls[job_id], len(script) - 1)]
        self._polls[job_id] += 1
        return JobLog(
            job_id=job_id, status=status, navigations=navigations, lines=lines
        )

    async def fetch_results(self, job_id: str) -> list[dict[str, Any]]:
        return ZEPTO_ROWS

    async def cancel_job(self, job_id: str) -> bool:
        self.cancels.append(job_id)
        return True

    async def fetch_screenshot(self, record: dict[str, Any], universe_id: str) -> bytes | None:
        return None

    async def aclose(self) -> None:
        return None


def live(settings, **overrides):
    """Live mode with only zepto wired, so a run is one universe and one job."""
    return dataclasses.replace(
        settings,
        bd_mode="live",
        bd_api_key="dummy-test-key",
        bd_base_url="https://api.brightdata.invalid",
        collector_ids={"zepto": "c_test01", "blinkit": "", "instamart": "", "chaos": ""},
        poll_interval_s=0.02,
        stall_retrigger_s=0.1,
        **overrides,
    )


@pytest.fixture
def fake_bd(monkeypatch):
    """Install a FakeBD built from `scripts` and hand it back for assertions."""

    def install(scripts) -> FakeBD:
        client = FakeBD(scripts)
        monkeypatch.setattr(runs_module, "build_client", lambda settings: client)
        return client

    return install


def start(client: TestClient) -> str:
    response = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


# -- the stall watchdog -------------------------------------------------------
def test_a_job_that_never_navigates_is_canceled_and_retriggered_once(settings, fake_bd) -> None:
    """The whole point: a job Bright Data never started running is not waited
    out, it is replaced — and the replacement delivers the rows."""
    bd = fake_bd([STALLED, [("running", 4, 0), ("done", 9, 6)]])

    with TestClient(create_app(live(settings, universe_timeout_s=10.0))) as client:
        snapshot = wait_for_done(client, start(client))

    events = snapshot["events"]
    retriggered = [e for e in events if e["type"] == "retriggered"]
    triggered = [e for e in events if e["type"] == "triggered"]

    assert len(retriggered) == 1
    assert retriggered[0]["universe"] == "zepto"
    assert retriggered[0]["data"]["job_id"] == "j_fake_1"  # the ABANDONED job
    assert retriggered[0]["data"]["reason"] == "job never started navigating"
    assert retriggered[0]["data"]["after_s"] >= 0.1
    # Canceled at Bright Data, not merely dropped: an abandoned job goes on
    # billing until something stops it.
    assert bd.cancels == ["j_fake_1"]
    assert bd.triggers == ["j_fake_1", "j_fake_2"]
    # The replacement is reported like any other job, so the UI has its id.
    assert [e["data"]["job_id"] for e in triggered] == ["j_fake_1", "j_fake_2"]
    assert [e["type"] for e in events].index("retriggered") < [
        e["type"] for e in events
    ].index("validated")
    assert snapshot["comparison"]["row_count"] == 6


def test_a_retrigger_leaves_the_universe_collecting_not_failed(settings, fake_bd) -> None:
    """`retriggered` is non-terminal. A universe that is being retried has not
    failed and must not be reported as if it had."""
    from server.events import IMPLEMENTED_EVENT_TYPES, TERMINAL_UNIVERSE_TYPES, EventType

    assert EventType.RETRIGGERED in IMPLEMENTED_EVENT_TYPES
    assert EventType.RETRIGGERED not in TERMINAL_UNIVERSE_TYPES

    fake_bd([STALLED, [("done", 9, 6)]])
    with TestClient(create_app(live(settings, universe_timeout_s=10.0))) as client:
        snapshot = wait_for_done(client, start(client))

    types = [e["type"] for e in snapshot["events"]]
    assert "failed" not in types
    assert "timed_out" not in types
    assert "validated" in types


@pytest.mark.parametrize(
    ("script", "why"),
    [
        ([("running", 3, 0)] * 12 + [("done", 9, 6)], "navigating, just slowly"),
        ([("running", 0, 4)] * 12 + [("done", 0, 6)], "delivering lines already"),
    ],
)
def test_a_slow_but_working_job_is_never_retriggered(settings, fake_bd, script, why) -> None:
    """The watchdog fires on evidence of a job that never started, not on time.
    A job well past the threshold that is doing something is left alone."""
    bd = fake_bd([script])

    with TestClient(create_app(live(settings, universe_timeout_s=10.0))) as client:
        snapshot = wait_for_done(client, start(client))

    types = [e["type"] for e in snapshot["events"]]
    assert "retriggered" not in types, why
    assert bd.cancels == []
    assert bd.triggers == ["j_fake_1"]
    assert "validated" in types


def test_a_universe_retriggers_at_most_once_then_times_out(settings, fake_bd) -> None:
    """A second stall is not this job's bad luck, and the deadline does NOT reset
    across a retrigger — the replacement inherits what is left of the universe's
    one timeout budget. So the universe times out rather than retrying forever."""
    bd = fake_bd([STALLED, STALLED])

    with TestClient(create_app(live(settings, universe_timeout_s=0.8))) as client:
        snapshot = wait_for_done(client, start(client))

    events = snapshot["events"]
    timed_out = [e for e in events if e["type"] == "timed_out"]

    assert len([e for e in events if e["type"] == "retriggered"]) == 1
    assert bd.triggers == ["j_fake_1", "j_fake_2"]
    assert len(timed_out) == 1 and timed_out[0]["data"]["after_s"] == 0.8
    # Both jobs stopped: the first by the watchdog, the second by the timeout.
    assert bd.cancels == ["j_fake_1", "j_fake_2"]
    assert events[-1]["type"] == "done"  # one universe timing out is not a run failure


# -- cancel on timeout --------------------------------------------------------
def test_a_timed_out_universe_cancels_its_job_at_bright_data(settings, fake_bd) -> None:
    """Giving up on a job in the app does not stop it at Bright Data. One was
    watched running 319s past the app's timeout, billing the whole time."""
    bd = fake_bd([[("running", 3, 0)]])  # navigating, so the watchdog stays out of it

    with TestClient(create_app(live(settings, universe_timeout_s=0.5))) as client:
        snapshot = wait_for_done(client, start(client))

    types = [e["type"] for e in snapshot["events"]]
    assert "retriggered" not in types
    assert "timed_out" in types
    assert bd.cancels == ["j_fake_1"]
    assert bd.triggers == ["j_fake_1"]


def test_a_failing_cancel_never_masks_the_timeout(settings, fake_bd) -> None:
    """The cancel is cleanup on a path that has already gone wrong. If it throws,
    what the run reports must not change by one word."""
    bd = fake_bd([[("running", 3, 0)]])

    async def refuse(job_id: str) -> bool:
        bd.cancels.append(job_id)
        raise RuntimeError("cancel endpoint down")

    bd.cancel_job = refuse  # type: ignore[method-assign]

    with TestClient(create_app(live(settings, universe_timeout_s=0.5))) as client:
        snapshot = wait_for_done(client, start(client))

    types = [e["type"] for e in snapshot["events"]]
    assert bd.cancels == ["j_fake_1"]  # it was tried
    assert "timed_out" in types
    assert "failed" not in types  # and it changed nothing
    assert types[-1] == "done"


# -- the daily live-run budget ------------------------------------------------
def test_a_live_run_over_the_daily_budget_is_refused_with_429(settings, fake_bd) -> None:
    """The per-client cooldown paces one visitor. Nothing paced the sum of them,
    so a day of demo traffic could spend the collector budget outright."""
    fake_bd([[("done", 9, 6)]])
    budgeted = live(settings, daily_run_budget=2, universe_timeout_s=10.0)

    with TestClient(create_app(budgeted)) as client:
        wait_for_done(client, start(client))
        wait_for_done(client, start(client))
        refused = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

    assert refused.status_code == 429
    assert refused.json()["detail"] == (
        "daily live-run budget reached, try tomorrow or watch the demo replay"
    )


def test_a_retrigger_spends_the_budget_too(settings, fake_bd) -> None:
    """A unit is one Bright Data TRIGGER, not one app run.

    The watchdog's replacement job costs exactly what the job it replaced cost,
    so counting only app runs under-reported the day's real spend by however many
    jobs were replaced. Here one run triggers twice and that is the whole budget.
    """
    bd = fake_bd([STALLED, [("running", 4, 0), ("done", 9, 6)]])
    budgeted = live(settings, daily_run_budget=2, universe_timeout_s=10.0)

    with TestClient(create_app(budgeted)) as client:
        wait_for_done(client, start(client))
        refused = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

    assert len(bd.triggers) == 2  # one run, two jobs
    assert refused.status_code == 429


def test_a_replay_does_not_spend_the_daily_budget(settings, fake_bd) -> None:
    """A replay re-streams a stored file and calls no collector, so charging it
    to a budget that exists to protect collector credits would refuse real runs
    to pay for something that costs nothing."""
    fake_bd([[("done", 9, 6)]])
    budgeted = live(settings, daily_run_budget=1, universe_timeout_s=10.0, replay_max_gap_s=0.0)

    with TestClient(create_app(budgeted)) as client:
        run_id = start(client)
        wait_for_done(client, run_id)

        replay = client.post(f"/api/replays/{run_id}")
        assert replay.status_code == 202
        wait_for_done(client, replay.json()["run_id"])

        # A replay ran, and the day's one live slot is still the only thing spent.
        second_live = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

    assert second_live.status_code == 429


def test_a_mock_run_does_not_spend_the_daily_budget(settings) -> None:
    """Mock mode drives fixtures off disk. There are no credits to protect, so
    the budget does not apply to it and a demo cannot be locked out by one."""
    budgeted = dataclasses.replace(settings, daily_run_budget=1)

    with TestClient(create_app(budgeted)) as client:
        codes = []
        for _ in range(3):
            response = client.post(
                "/api/runs", json={"query": "amul butter", "pincode": "560001"}
            )
            codes.append(response.status_code)
            wait_for_done(client, response.json()["run_id"])

    assert codes == [202, 202, 202]


def test_the_daily_budget_resets_when_the_utc_date_changes(settings, fake_bd, monkeypatch) -> None:
    """Keyed on the UTC date rather than a rolling window, so the budget is one
    day's worth of runs and not a permanent cap."""
    fake_bd([[("done", 9, 6)]])
    budgeted = live(settings, daily_run_budget=1, universe_timeout_s=10.0)
    today = ["2026-08-23"]
    monkeypatch.setattr(runs_module, "_utc_date", lambda: today[0])

    with TestClient(create_app(budgeted)) as client:
        wait_for_done(client, start(client))
        refused = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

        today[0] = "2026-08-24"
        tomorrow = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        wait_for_done(client, tomorrow.json()["run_id"])

    assert refused.status_code == 429
    assert tomorrow.status_code == 202
