"""End-to-end smoke over the HTTP surface with BD_MODE=mock — DESIGN.md §7, §11."""

from __future__ import annotations

import dataclasses
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for_done
from server.app import create_app

LIFECYCLE = [
    "run_requested",
    "universe_dispatched",
    "triggered",
    "progress",
    "progress",
    "rows",
    "screenshot",
    "validated",
    "done",
]


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_universes_reports_wiring_honestly(client: TestClient) -> None:
    body = client.get("/api/universes").json()
    rows = {u["id"]: u for u in body["universes"]}

    assert body["mode"] == "mock"
    assert [u["id"] for u in body["universes"]] == ["zepto", "blinkit", "instamart"]
    # No collector id is configured in tests, so nothing claims to be wired.
    assert all(u["wired"] is False for u in rows.values())
    # Mock mode still dispatches zepto, because it has a fixture and a real mapper.
    assert rows["zepto"]["dispatchable"] is True and rows["zepto"]["status"] == "mock"
    assert rows["blinkit"]["dispatchable"] is False
    # No area list any more: any valid Indian pincode is accepted, so there is
    # nothing for the server to enumerate.
    assert "areas" not in body


def test_a_universe_with_no_mapper_is_not_offered_to_the_ui(client: TestClient, settings) -> None:
    """`chaos` was never built. Its registry row is the record of the shape a
    fourth universe would take, and its stub mapper still refuses rather than
    reporting empty rows — but the API does not offer it, because a permanently
    dead "no mapper yet" chip reads as a broken feature rather than an unbuilt
    one. A universe with a real mapper is always listed, wired or not.
    """
    from server.registry import listed, universes

    body = client.get("/api/universes").json()

    assert "chaos" not in {u["id"] for u in body["universes"]}
    assert "chaos" in {u.id for u in universes(settings)}  # the row is still there
    assert "chaos" not in {u.id for u in listed(settings)}
    # blinkit has a real mapper and no fixture in mock mode: not dispatchable,
    # still listed, and honest about why.
    blinkit = next(u for u in body["universes"] if u["id"] == "blinkit")
    assert (blinkit["dispatchable"], blinkit["status"]) == (False, "no fixture")


def test_no_collector_id_ever_leaves_the_process(client: TestClient) -> None:
    body = client.get("/api/universes").text

    assert "collector_id" in body
    assert all(u["collector_id"] == "" for u in json.loads(body)["universes"])


def test_mock_run_streams_the_whole_lifecycle_and_reaches_done(client: TestClient) -> None:
    created = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    snapshot = wait_for_done(client, run_id)
    types = [e["type"] for e in snapshot["events"]]

    assert types == LIFECYCLE
    assert [e["i"] for e in snapshot["events"]] == list(range(1, len(types) + 1))
    assert all(e["replay"] is False for e in snapshot["events"])
    assert snapshot["meta"]["status"] == "done"
    assert snapshot["meta"]["mode"] == "mock"


def test_validated_event_reports_the_dropped_row(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]

    snapshot = wait_for_done(client, run_id)
    validated = next(e for e in snapshot["events"] if e["type"] == "validated")

    # The fixture's zero-priced row is the one that must not survive the gate.
    assert validated["data"]["rows_kept"] == 6
    assert validated["data"]["rows_dropped"] == 1
    assert validated["data"]["reasons"] == {"no_price": 1}


def test_snapshot_carries_comparison_groups_from_the_fixture(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]

    comparison = wait_for_done(client, run_id)["comparison"]
    everything = comparison["groups"] + comparison["unmatched"]

    assert comparison["row_count"] == 6
    assert len(everything) == 6
    assert {g["confidence"] for g in everything} == {"unmatched"}  # only zepto ran
    names = {r["name"] for g in everything for r in g["rows"]}
    assert "Amul Salted Butter" in names
    assert "Heritage Table Butter" not in names  # dropped by the validation gate


def test_sse_stream_delivers_the_lifecycle_in_order(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]

    events = read_sse(client, f"/api/runs/{run_id}/events")

    assert [e["type"] for e in events] == LIFECYCLE
    assert [e["i"] for e in events] == list(range(1, len(LIFECYCLE) + 1))


def test_sse_resumes_from_last_event_id(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    wait_for_done(client, run_id)

    resumed = read_sse(client, f"/api/runs/{run_id}/events", last_event_id="5")

    assert [e["i"] for e in resumed] == [6, 7, 8, 9]


def test_screenshot_artifact_is_served(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    snapshot = wait_for_done(client, run_id)
    shot = next(e for e in snapshot["events"] if e["type"] == "screenshot")

    response = client.get(shot["data"]["url"])

    assert shot["data"]["placeholder"] is True
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_artifact_path_traversal_is_refused(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    wait_for_done(client, run_id)

    assert client.get(f"/api/runs/{run_id}/artifacts/..%2F..%2Fmeta.json").status_code == 404
    assert client.get(f"/api/runs/{run_id}/artifacts/nope.png").status_code == 404


def test_replay_relabels_every_event(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    original = wait_for_done(client, run_id)["events"]

    replay_id = client.post(f"/api/replays/{run_id}").json()["run_id"]
    replayed = wait_for_done(client, replay_id)["events"]

    assert replay_id.startswith("rp_")
    assert all(e["replay"] is True for e in replayed)
    assert [e["type"] for e in replayed] == [e["type"] for e in original]
    assert [e["ts"] for e in replayed] == [e["ts"] for e in original]
    assert [e["i"] for e in replayed] == [e["i"] for e in original]


def test_replay_of_a_replay_is_refused(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    wait_for_done(client, run_id)
    replay_id = client.post(f"/api/replays/{run_id}").json()["run_id"]
    wait_for_done(client, replay_id)

    rejected = client.post(f"/api/replays/{replay_id}")

    assert rejected.status_code == 400
    assert "replay" in rejected.json()["detail"]


def test_replay_screenshot_points_at_the_replay_run(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    wait_for_done(client, run_id)
    replay_id = client.post(f"/api/replays/{run_id}").json()["run_id"]

    events = wait_for_done(client, replay_id)["events"]
    shot = next(e for e in events if e["type"] == "screenshot")

    assert shot["data"]["url"] == f"/api/runs/{replay_id}/artifacts/zepto.png"
    assert client.get(shot["data"]["url"]).status_code == 200


def test_any_well_formed_indian_pincode_is_accepted(settings) -> None:
    """Open inputs: the app is for live users, not one demo address. There is no
    allowlist and no coordinate lookup to fail against."""
    with TestClient(create_app(settings)) as client:
        accepted = [
            client.post("/api/runs", json={"query": "amul butter", "pincode": p}).status_code
            for p in ("560001", "110001", "400001", "700001")
        ]

    assert accepted == [202, 202, 202, 202]


@pytest.mark.parametrize(
    "pincode",
    ["", "56009", "5600011", "060097", "56o097", "560 097", "abcdef", "-560001"],
)
def test_a_pincode_that_is_not_six_digits_is_refused_with_a_reason(
    client: TestClient, pincode: str
) -> None:
    response = client.post("/api/runs", json={"query": "amul butter", "pincode": pincode})

    assert response.status_code == 400
    assert "pincode" in response.json()["detail"]


def test_a_trailing_space_is_trimmed_rather_than_refused(client: TestClient) -> None:
    """`"560001 "` is a person hitting space, not a bad pincode."""
    assert client.post(
        "/api/runs", json={"query": "amul butter", "pincode": " 560001 "}
    ).status_code == 202


@pytest.mark.parametrize("query", ["", "   ", "a", " x "])
def test_a_query_that_is_too_short_is_refused(client: TestClient, query: str) -> None:
    response = client.post("/api/runs", json={"query": query, "pincode": "560001"})

    assert response.status_code == 400
    assert "query" in response.json()["detail"]


def test_a_query_longer_than_sixty_characters_is_refused(client: TestClient) -> None:
    response = client.post("/api/runs", json={"query": "b" * 61, "pincode": "560001"})

    assert response.status_code == 400
    assert "60" in response.json()["detail"]


def test_a_query_with_control_characters_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/runs", json={"query": "amul\x07butter", "pincode": "560001"}
    )

    assert response.status_code == 400
    assert "printable" in response.json()["detail"]


def test_whitespace_in_a_query_is_collapsed_before_it_is_stored(client: TestClient) -> None:
    """The stored query is what the receipt shows and what the collector was
    asked for, so it is the normalised one — not the raw keystrokes."""
    created = client.post(
        "/api/runs", json={"query": "  amul   salted \t butter ", "pincode": "560001"}
    )

    assert created.status_code == 202
    assert created.json()["meta"]["query"] == "amul salted butter"


def test_the_run_records_the_pincode_as_its_own_area_label(client: TestClient) -> None:
    """There is no lookup table to name an area, so the label IS the pincode.
    What the SITE resolved it to travels on the rows instead."""
    meta = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["meta"]

    assert meta["pincode"] == "560001"
    assert meta["area_label"] == "560001"


def test_a_second_run_from_one_client_is_throttled_with_a_429(settings) -> None:
    """The open inputs need a brake: one visitor must not be able to spend the
    whole collector budget."""
    cooled = dataclasses.replace(settings, run_cooldown_s=60.0, max_concurrent_runs=10)
    with TestClient(create_app(cooled)) as client:
        first = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        second = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

    assert first.status_code == 202
    assert second.status_code == 429
    assert "one run per 60s" in second.json()["detail"]
    assert "try again in" in second.json()["detail"]


def test_a_refused_request_does_not_start_the_cooldown(settings) -> None:
    """A bad pincode must not cost the client its next minute — otherwise a typo
    locks a visitor out of the app."""
    cooled = dataclasses.replace(settings, run_cooldown_s=60.0, max_concurrent_runs=10)
    with TestClient(create_app(cooled)) as client:
        rejected = client.post("/api/runs", json={"query": "amul butter", "pincode": "1"})
        accepted = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

    assert rejected.status_code == 400
    assert accepted.status_code == 202


def test_the_cooldown_is_per_client_and_can_be_switched_off(settings) -> None:
    with TestClient(create_app(dataclasses.replace(settings, run_cooldown_s=0.0))) as client:
        codes = [
            client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"}).status_code
            for _ in range(3)
        ]

    assert codes == [202, 202, 202]


def test_query_allowlist_is_enforced_when_set(settings) -> None:
    restricted = dataclasses.replace(settings, query_allowlist=("amul butter",))
    with TestClient(create_app(restricted)) as client:
        allowed = client.post("/api/runs", json={"query": "Amul Butter", "pincode": "560001"})
        refused = client.post("/api/runs", json={"query": "bananas", "pincode": "560001"})

    assert allowed.status_code == 202  # allowlist match is case-insensitive
    assert refused.status_code == 400
    assert "allowlist" in refused.json()["detail"]


def test_rate_limit_refuses_with_an_honest_reason(settings) -> None:
    """429, not 400. Nothing is wrong with the request — a 400 tells the client
    to change its input, which is exactly the wrong advice when the fix is to
    wait."""
    limited = dataclasses.replace(settings, rate_limit_per_min=2, max_concurrent_runs=10)
    with TestClient(create_app(limited)) as client:
        codes = [
            client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"}).status_code
            for _ in range(3)
        ]
        detail = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["detail"]

    assert codes == [202, 202, 429]
    assert "rate limit" in detail


def test_concurrency_cap_refuses_a_second_in_flight_run(settings) -> None:
    """Also 429: the request is fine, the moment is not."""
    serial = dataclasses.replace(settings, max_concurrent_runs=1, mock_step_delay_s=0.2)
    with TestClient(create_app(serial)) as client:
        first = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        second = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        wait_for_done(client, first.json()["run_id"])

    assert first.status_code == 202
    assert second.status_code == 429
    assert "already in flight" in second.json()["detail"]


def test_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.get("/api/runs/r_20200101_000000_dead").status_code == 404
    assert client.get("/api/runs/not-a-run-id").status_code == 404


def test_runs_listing_includes_the_new_run(client: TestClient) -> None:
    run_id = client.post(
        "/api/runs", json={"query": "amul butter", "pincode": "560001"}
    ).json()["run_id"]
    wait_for_done(client, run_id)

    runs = client.get("/api/runs").json()["runs"]

    assert run_id in {r["run_id"] for r in runs}


def read_sse(client: TestClient, url: str, last_event_id: str | None = None) -> list[dict]:
    """Collect SSE frames until `done`, the stream ends, or we run out of patience."""
    headers = {"Last-Event-ID": last_event_id} if last_event_id else {}
    collected: list[dict] = []
    deadline = time.monotonic() + 15.0

    with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if time.monotonic() > deadline:
                raise AssertionError("SSE stream did not finish in time")
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: "):])
            collected.append(event)
            if event["type"] == "done":
                break
    return collected
