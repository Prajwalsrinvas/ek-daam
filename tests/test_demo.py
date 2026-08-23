"""The curated demo list: server/demo.py.

Every run id this file names is PUBLIC - readable and replayable by anyone,
whoever captured it. So two things are pinned here: that a listed capture really
does open for a stranger, and that a file an operator edited badly degrades to
"nothing curated" rather than to a 500 on the endpoint every visitor hits first.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for_done
from server.app import create_app
from server.demo import load_demo_entries, parse_entries, public_run_ids

RUN_A = "r_20260823_120000_aa01"
RUN_B = "r_20260823_120500_bb02"


def stored(*run_ids: str):
    """A stand-in for the run store: these ids exist, nothing else does."""
    known = {run_id: "amul butter" for run_id in run_ids}
    return lambda run_id: known.get(run_id)


def entry(**overrides) -> dict:
    base = {
        "id": "party-cart",
        "title": "Party for eight",
        "note": "four items, basket total per app",
        "kind": "run",
        "run_ids": [RUN_A],
    }
    return {**base, **overrides}


# -- parsing ------------------------------------------------------------------
def test_a_well_formed_entry_is_kept() -> None:
    entries = parse_entries([entry()], stored(RUN_A))

    assert [e.id for e in entries] == ["party-cart"]
    assert entries[0].kind == "run"


def test_an_entry_naming_a_run_that_is_not_stored_is_skipped() -> None:
    """A list naming a capture that was never copied to the deployment would
    offer the judges a button that 404s."""
    assert parse_entries([entry()], stored()) == []


@pytest.mark.parametrize(
    "bad",
    [
        entry(kind="run", run_ids=[RUN_A, RUN_B]),  # a run is exactly one
        entry(kind="cart", run_ids=[RUN_A, RUN_B], items=["chips"]),  # one name per run
        entry(kind="story", run_ids=[RUN_A, RUN_B], chapters=["healthy"]),
        entry(run_ids=[]),
        entry(id=""),
        entry(title=""),
        entry(kind="playlist"),
        {"nonsense": True},
        "not an object",
    ],
)
def test_an_entry_of_the_wrong_shape_is_skipped_not_raised(bad) -> None:
    assert parse_entries([bad], stored(RUN_A, RUN_B)) == []


def test_a_bad_entry_does_not_take_the_good_ones_with_it() -> None:
    entries = parse_entries(
        [entry(id="broken", run_ids=["r_20200101_000000_dead"]), entry(id="fine")],
        stored(RUN_A),
    )

    assert [e.id for e in entries] == ["fine"]


def test_two_entries_with_one_id_keep_the_first() -> None:
    entries = parse_entries(
        [entry(title="first"), entry(title="second", run_ids=[RUN_B])],
        stored(RUN_A, RUN_B),
    )

    assert [e.title for e in entries] == ["first"]


def test_a_cart_entry_carries_its_items_in_run_order() -> None:
    entries = parse_entries(
        [entry(kind="cart", run_ids=[RUN_A, RUN_B], items=["chips", "cake"])],
        stored(RUN_A, RUN_B),
    )

    assert entries[0].items == ["chips", "cake"]
    assert entries[0].run_ids == [RUN_A, RUN_B]


def test_a_file_that_is_not_a_list_is_an_empty_list() -> None:
    assert parse_entries({"id": "party"}, stored(RUN_A)) == []


# -- loading ------------------------------------------------------------------
def test_a_missing_file_is_an_empty_list(tmp_path: Path) -> None:
    assert load_demo_entries(tmp_path / "nope.json", stored(RUN_A)) == []


def test_a_file_that_is_not_json_is_an_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "demo.json"
    path.write_text("{not json", encoding="utf-8")

    assert load_demo_entries(path, stored(RUN_A)) == []


def test_the_legacy_single_id_is_appended_with_the_runs_own_query(tmp_path: Path) -> None:
    path = tmp_path / "demo.json"
    path.write_text(json.dumps([entry()]), encoding="utf-8")

    entries = load_demo_entries(path, stored(RUN_A, RUN_B), legacy_run_id=RUN_B)

    assert [e.id for e in entries] == ["party-cart", "demo"]
    assert entries[-1].title == "amul butter"
    assert entries[-1].run_ids == [RUN_B]


def test_the_legacy_id_is_not_added_twice(tmp_path: Path) -> None:
    path = tmp_path / "demo.json"
    path.write_text(json.dumps([entry(run_ids=[RUN_A])]), encoding="utf-8")

    entries = load_demo_entries(path, stored(RUN_A), legacy_run_id=RUN_A)

    assert [e.id for e in entries] == ["party-cart"]


def test_the_legacy_id_stays_public_even_when_its_run_is_gone() -> None:
    """It was public before this file existed. A run that is not on disk is a 404
    on its own, so nothing is granted that was not there anyway."""
    assert public_run_ids([], legacy_run_id=RUN_B) == frozenset({RUN_B})


# -- over HTTP ----------------------------------------------------------------
def write_demo(settings, entries: list[dict]) -> None:
    settings.demo_list_path.parent.mkdir(parents=True, exist_ok=True)
    settings.demo_list_path.write_text(json.dumps(entries), encoding="utf-8")


def test_the_list_is_empty_until_one_is_curated(settings) -> None:
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/universes").json()

    assert body["demo"] == []
    assert body["demo_run_id"] is None


def test_every_run_the_list_names_is_public(settings) -> None:
    """The whole point of the file: a stranger opens the capture, replays it and
    reads its artifacts without having run anything."""
    with TestClient(create_app(settings)) as author:
        first = author.post(
            "/api/runs", json={"query": "chips", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(author, first)
        second = author.post(
            "/api/runs", json={"query": "cake", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(author, second)

    write_demo(
        settings,
        [
            {
                "id": "party-cart",
                "title": "Party for eight",
                "note": "two items, basket total per app",
                "kind": "cart",
                "run_ids": [first, second],
                "items": ["chips", "cake"],
            }
        ],
    )

    with TestClient(create_app(settings)) as visitor:
        listed = visitor.get("/api/universes").json()["demo"]
        snapshot = visitor.get(f"/api/runs/{first}")
        events = visitor.get(f"/api/runs/{first}/events")
        artifact = visitor.get(f"/api/runs/{second}/artifacts/zepto.png")
        replay = visitor.post(f"/api/replays/{second}")

    assert [e["id"] for e in listed] == ["party-cart"]
    assert listed[0]["items"] == ["chips", "cake"]
    assert snapshot.status_code == 200
    assert events.status_code == 200
    assert artifact.status_code == 200
    assert replay.status_code == 202


def test_a_run_the_list_does_not_name_is_still_private(settings) -> None:
    with TestClient(create_app(settings)) as author:
        public = author.post(
            "/api/runs", json={"query": "chips", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(author, public)
        private = author.post(
            "/api/runs", json={"query": "cake", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(author, private)

    write_demo(settings, [{"id": "one", "title": "Chips", "kind": "run", "run_ids": [public]}])

    with TestClient(create_app(settings)) as visitor:
        assert visitor.get(f"/api/runs/{public}").status_code == 200
        assert visitor.get(f"/api/runs/{private}").status_code == 404


def test_a_public_run_does_not_join_a_strangers_listing(settings) -> None:
    """Public to open, not theirs to own: a demo capture in every visitor's
    listing would look like a search they had run."""
    with TestClient(create_app(settings)) as author:
        run_id = author.post(
            "/api/runs", json={"query": "chips", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(author, run_id)

    write_demo(settings, [{"id": "one", "title": "Chips", "kind": "run", "run_ids": [run_id]}])

    with TestClient(create_app(settings)) as visitor:
        assert visitor.get("/api/runs").json()["runs"] == []


def test_a_broken_list_never_500s_the_endpoint_everyone_loads_first(settings) -> None:
    settings.demo_list_path.parent.mkdir(parents=True, exist_ok=True)
    settings.demo_list_path.write_text("[[[", encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/universes")

    assert response.status_code == 200
    assert response.json()["demo"] == []


def test_the_list_file_can_live_anywhere(settings, tmp_path: Path) -> None:
    """`SVERSE_DEMO_FILE` points at it; unset, it is `demo.json` inside the runs
    directory, so it moves with the runs rather than being pinned to the repo."""
    elsewhere = tmp_path / "curated" / "list.json"
    moved = dataclasses.replace(settings, demo_file=elsewhere)
    with TestClient(create_app(moved)) as author:
        run_id = author.post(
            "/api/runs", json={"query": "chips", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(author, run_id)

    write_demo(moved, [{"id": "one", "title": "Chips", "kind": "run", "run_ids": [run_id]}])

    with TestClient(create_app(moved)) as visitor:
        assert [e["id"] for e in visitor.get("/api/universes").json()["demo"]] == ["one"]
        assert visitor.get(f"/api/runs/{run_id}").status_code == 200


def test_editing_the_list_needs_no_restart(settings) -> None:
    """An operator adding a capture ten minutes before a demo must not have to
    restart the server to publish it."""
    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "chips", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

        before = client.get("/api/universes").json()["demo"]
        write_demo(settings, [{"id": "one", "title": "Chips", "kind": "run", "run_ids": [run_id]}])
        after = client.get("/api/universes").json()["demo"]

    assert before == []
    assert [e["id"] for e in after] == ["one"]
