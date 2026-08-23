"""Validation gate, zero-rows taxonomy, and the invariant that one universe
failing never takes the run down. DESIGN.md §5.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import mappers
from server.app import create_app
from server.resolve import NormalizedRow
from server.runs import validate_rows, zero_rows_reason

from conftest import FIXTURES, carry_cookies, wait_for_done


RESOLVED_560001 = "Bengaluru - 560001, Karnataka"


def row(**overrides) -> NormalizedRow:
    base = dict(
        universe="zepto",
        name="Amul Salted Butter",
        price=309.0,
        in_stock=True,
        # Every row a real mapper produces carries what the site resolved; the
        # location proof reads it, so a hand-built row needs it too.
        resolved_area=RESOLVED_560001,
    )
    return NormalizedRow(**{**base, **overrides})


# -- validation gate ----------------------------------------------------------
def test_gate_keeps_a_sane_row() -> None:
    kept, dropped, reasons = validate_rows([row()])

    assert len(kept) == 1 and dropped == 0 and reasons == {}


@pytest.mark.parametrize(
    ("bad", "reason"),
    [
        (row(name="  "), "no_name"),
        (row(price=None), "no_price"),
        (row(price=0.0), "no_price"),
        (row(price=-5.0), "no_price"),
        (row(price=0.5), "price_out_of_band"),
        (row(price=10_001.0), "price_out_of_band"),
    ],
)
def test_gate_drops_and_names_the_reason(bad: NormalizedRow, reason: str) -> None:
    kept, dropped, reasons = validate_rows([bad])

    assert kept == [] and dropped == 1
    assert reasons == {reason: 1}


def test_gate_accepts_the_band_edges() -> None:
    kept, dropped, _ = validate_rows([row(price=1.0), row(price=10_000.0)])

    assert len(kept) == 2 and dropped == 0


# -- zero-rows taxonomy -------------------------------------------------------
def test_all_out_of_stock_is_oos() -> None:
    assert zero_rows_reason([row(in_stock=False), row(in_stock=False)], {}) == "oos"


def test_one_in_stock_row_is_not_oos() -> None:
    assert zero_rows_reason([row(in_stock=False), row(in_stock=True)], {}) == "broken"


def test_site_saying_unserviceable_is_reported_as_such() -> None:
    assert zero_rows_reason([], {"message": "This location is unserviceable"}) == "unserviceable"


def test_block_signals_are_reported_as_blocked() -> None:
    assert zero_rows_reason([], {"error": "captcha challenge"}) == "blocked"
    assert zero_rows_reason([], {"status": "HTTP 429 rate limited"}) == "blocked"


def test_ambiguous_emptiness_defaults_to_broken() -> None:
    """The honest default: an empty collector result is not evidence of anything
    about the store, so it must not claim `oos` or `unserviceable`."""
    assert zero_rows_reason([], {}) == "broken"
    assert zero_rows_reason([], {"note": "who knows"}) == "broken"


# -- run isolation ------------------------------------------------------------
@pytest.fixture
def two_universe_settings(settings, tmp_path: Path):
    """Give blinkit a fixture and a working mapper slot so it actually dispatches."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    shutil.copy(FIXTURES / "zepto_collector_result.json", fixtures / "zepto_collector_result.json")
    payload = json.loads((FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8"))
    payload[0]["universe"] = "blinkit"
    (fixtures / "blinkit_collector_result.json").write_text(json.dumps(payload), encoding="utf-8")
    return dataclasses.replace(settings, fixtures_dir=fixtures)


def test_a_missing_screenshot_is_not_a_universe_failure(settings, monkeypatch) -> None:
    """`artifact_failed` is non-terminal. The capture is evidence so its absence
    stays visible, but the rows are good and the universe still validates —
    reporting `failed` here put a red line in the feed for a success."""

    async def boom(self, record, universe_id):
        raise RuntimeError("artifact host down")

    monkeypatch.setattr("server.bd_client.MockClient.fetch_screenshot", boom)

    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]
    artifact = next(e for e in snapshot["events"] if e["type"] == "artifact_failed")

    assert "failed" not in types
    assert "screenshot" not in types
    assert types.index("artifact_failed") < types.index("validated")
    assert "artifact host down" in artifact["data"]["error"]
    assert artifact["universe"] == "zepto"
    assert snapshot["comparison"]["row_count"] == 6  # the rows still stand
    assert types[-1] == "done"


def test_artifact_failed_is_not_a_terminal_state() -> None:
    from server.events import IMPLEMENTED_EVENT_TYPES, TERMINAL_UNIVERSE_TYPES, EventType

    assert EventType.ARTIFACT_FAILED in IMPLEMENTED_EVENT_TYPES
    assert EventType.ARTIFACT_FAILED not in TERMINAL_UNIVERSE_TYPES


def test_one_universe_failing_does_not_kill_the_run(two_universe_settings, monkeypatch) -> None:
    def exploding(payload):
        raise RuntimeError("mapper blew up")

    monkeypatch.setitem(mappers.MAPPERS, "blinkit", exploding)

    with TestClient(create_app(two_universe_settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    events = snapshot["events"]
    failed = [e for e in events if e["type"] == "failed"]
    validated = [e for e in events if e["type"] == "validated"]

    assert [e["universe"] for e in failed] == ["blinkit"]
    assert "mapper blew up" in failed[0]["data"]["error"]
    assert [e["universe"] for e in validated] == ["zepto"]  # the healthy one still landed
    assert events[-1]["type"] == "done"
    assert snapshot["comparison"]["row_count"] == 6


def test_all_rows_out_of_stock_reports_zero_rows_not_failure(settings, monkeypatch) -> None:
    def all_oos(payload):
        return [row(in_stock=False, name="Amul Salted Butter", price=309.0)]

    monkeypatch.setitem(mappers.MAPPERS, "zepto", all_oos)

    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]
    zero = next(e for e in snapshot["events"] if e["type"] == "zero_rows")

    assert "validated" not in types
    assert zero["data"]["reason"] == "oos"
    assert types[-1] == "done"


def test_a_curated_replay_under_runs_replays_is_streamable(settings) -> None:
    """`runs/` is gitignored except `runs/replays/`, so a committed demo capture
    has to be reachable from a fresh checkout with no prior run in memory."""
    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

    curated = settings.runs_dir / "replays" / run_id
    curated.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(settings.runs_dir / run_id), str(curated))

    # Fresh app: nothing about that run is in memory any more. Same visitor,
    # so the capture is still theirs to read and replay.
    with TestClient(create_app(settings)) as restarted:
        client = carry_cookies(client, restarted)
        snapshot = client.get(f"/api/runs/{run_id}")
        listed = client.get("/api/runs").json()["runs"]
        replay_id = client.post(f"/api/replays/{run_id}").json()["run_id"]
        replayed = wait_for_done(client, replay_id)
        artifact = client.get(f"/api/runs/{run_id}/artifacts/zepto.png")

    assert snapshot.status_code == 200
    assert snapshot.json()["comparison"]["row_count"] == 6  # re-derived from raw/
    assert run_id in {r["run_id"] for r in listed}
    assert all(e["replay"] is True for e in replayed["events"])
    assert artifact.status_code == 200


def test_a_second_universe_produces_a_real_cross_universe_match(
    two_universe_settings, monkeypatch
) -> None:
    """The comparison table only has something to show once two sources agree on
    brand, pack size and variant."""
    zepto_mapper = mappers.MAPPERS["zepto"]

    def as_blinkit(payload):
        return [r.model_copy(update={"universe": "blinkit"}) for r in zepto_mapper(payload)]

    monkeypatch.setitem(mappers.MAPPERS, "blinkit", as_blinkit)

    with TestClient(create_app(two_universe_settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        comparison = wait_for_done(client, run_id)["comparison"]

    assert comparison["unmatched"] == []
    assert len(comparison["groups"]) == 6
    assert all(g["confidence"] == "close" for g in comparison["groups"])
    assert all(g["universes"] == ["blinkit", "zepto"] for g in comparison["groups"])


# -- replays are runs too, and were the one ungated entrance ------------------
def test_a_replay_does_not_occupy_the_single_live_slot(settings) -> None:
    """A replay re-streams a file and calls no collector, so making it hold the
    one live slot would block a real run for nothing."""
    serial = dataclasses.replace(settings, max_concurrent_runs=1, replay_max_gap_s=0.05)
    with TestClient(create_app(serial)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

        replay = client.post(f"/api/replays/{run_id}")
        # A live run may start while that replay is still streaming.
        live = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        wait_for_done(client, replay.json()["run_id"])
        wait_for_done(client, live.json()["run_id"])

    assert replay.status_code == 202
    assert live.status_code == 202


def test_only_one_replay_per_client_streams_at_a_time(settings) -> None:
    """The replay endpoint had no guard at all: any client could open unbounded
    concurrent re-streams, each holding a task, a file handle and every event in
    memory."""
    paced = dataclasses.replace(settings, replay_max_gap_s=2.0)
    with TestClient(create_app(paced)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

        first = client.post(f"/api/replays/{run_id}")
        second = client.post(f"/api/replays/{run_id}")

    assert first.status_code == 202
    assert second.status_code == 429
    assert "already streaming" in second.json()["detail"]


def test_a_replay_respects_a_cooldown_of_its_own(settings) -> None:
    """Paced, but out of its own budget: a replay spends no collector credits, so
    charging it to the live cooldown would refuse the demo replay for a minute
    after every live run."""
    cooled = dataclasses.replace(settings, run_cooldown_s=60.0, replay_max_gap_s=0.0)
    with TestClient(create_app(cooled)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

        # The live run just stamped the live cooldown; the replay is unaffected.
        first = client.post(f"/api/replays/{run_id}")
        wait_for_done(client, first.json()["run_id"])
        second = client.post(f"/api/replays/{run_id}")

    assert first.status_code == 202
    assert second.status_code == 429
    assert "one replay per 60s" in second.json()["detail"]


def test_a_run_that_never_finished_cannot_be_replayed(settings, tmp_path: Path) -> None:
    """A run still in flight has a file that is still being appended to, and a
    failed one is a partial record. Re-streaming either presents an incomplete
    capture as a complete one."""
    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

    meta_path = settings.runs_dir / run_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = "failed"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    with TestClient(create_app(settings)) as restarted:
        client = carry_cookies(client, restarted)
        refused = client.post(f"/api/replays/{run_id}")

    assert refused.status_code == 400
    assert "not done" in refused.json()["detail"]


def test_finished_runs_are_dropped_from_memory_but_stay_readable(settings) -> None:
    """Everything a finished run holds is already on disk, and every read path
    falls back to it. Without pruning, a long-lived process keeps every event and
    every parsed row of every run it has ever served."""
    from server.runs import MEMORY_RUNS_KEPT

    with TestClient(create_app(settings)) as client:
        manager = client.app.state.runs
        ids = []
        for _ in range(MEMORY_RUNS_KEPT + 3):
            run_id = client.post(
                "/api/runs", json={"query": "amul butter", "pincode": "560001"}
            ).json()["run_id"]
            wait_for_done(client, run_id)
            ids.append(run_id)

        in_memory = set(manager._metas)
        oldest = client.get(f"/api/runs/{ids[0]}")

    assert len(in_memory) <= MEMORY_RUNS_KEPT
    assert ids[0] not in in_memory
    assert ids[-1] in in_memory
    # Dropped from memory, still fully served from disk.
    assert oldest.status_code == 200
    assert oldest.json()["comparison"]["row_count"] == 6
    assert [e["type"] for e in oldest.json()["events"]][-1] == "done"


def test_a_cancelled_run_is_recorded_as_cancelled_not_failed(settings) -> None:
    """A shutdown is not a collector failure. Recording it as one puts a red line
    in a stored run for something that never happened; leaving the status at
    "running" strands the run forever."""
    slow = dataclasses.replace(settings, mock_step_delay_s=5.0)
    with TestClient(create_app(slow)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        # Leaving the context manager runs lifespan shutdown, which cancels it.

    meta = json.loads((slow.runs_dir / run_id / "meta.json").read_text(encoding="utf-8"))
    events = (slow.runs_dir / run_id / "events.jsonl").read_text(encoding="utf-8")

    assert meta["status"] == "cancelled"
    assert meta["finished_at"] is not None
    assert '"failed"' not in events


def test_a_live_run_without_an_api_key_is_refused_before_it_exists(settings) -> None:
    """The client refuses to start without BD_API_KEY. Finding that out inside
    the run task created a run that was stranded the moment it was created: a run
    id, a directory and a `run_requested` event the caller had already been told
    202 about."""
    keyless = dataclasses.replace(settings, bd_mode="live", bd_api_key="", collector_ids={
        "zepto": "c_x", "blinkit": "", "instamart": "", "chaos": ""
    })
    with TestClient(create_app(keyless)) as client:
        refused = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        listed = client.get("/api/runs").json()["runs"]

    assert refused.status_code == 400
    assert "BD_API_KEY" in refused.json()["detail"]
    assert listed == []  # no run was ever created
