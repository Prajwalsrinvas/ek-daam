"""The model-assisted matching layer: server/llm_match.py.

No test here reaches the network. `_ask_block` is the only function in the module
that talks to OpenRouter, so every test stubs exactly that one and drives the
real blocking, rendering, guards, timeout and event code above it.

What is being pinned is not "the model is right" - it is not this code's job to
be right - but that a wrong answer cannot hurt: the guards refuse it, the rows
shown are always the captured ones, and the deterministic receipt is untouched
whatever comes back.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for_done
from server import llm_match, mappers
from server import runs as runs_module
from server.app import create_app
from server.events import EventStore, EventType
from server.llm_match import (
    CompactCluster,
    assign_ids,
    block_key,
    candidate_blocks,
    collect_groups,
    real_rows,
    render_block,
    run_llm_match,
)
from server.resolve import NormalizedRow, match

RESOLVED_560001 = "Bengaluru - 560001, Karnataka"


def row(universe: str, name: str, **overrides) -> NormalizedRow:
    base = dict(
        universe=universe,
        name=name,
        brand="Amul",
        qty=500.0,
        unit="g",
        price=310.0,
        in_stock=True,
        product_id=f"{universe}-{name}",
        resolved_area=RESOLVED_560001,
    )
    return NormalizedRow(**{**base, **overrides})


# The pair the whole layer exists for: the same 500 g pack of the same butter
# under two listing names no token overlap can join.
PASTEURISED = row("instamart", "Amul Pasteurised Butter", price=310.0)
SALTED = row("zepto", "Amul Salted Butter", price=309.0)


def rows_by_universe(*rows: NormalizedRow) -> dict[str, list[NormalizedRow]]:
    out: dict[str, list[NormalizedRow]] = {}
    for item in rows:
        out.setdefault(item.universe, []).append(item)
    return out


def by_id_for(*rows: NormalizedRow) -> dict[str, NormalizedRow]:
    return dict(assign_ids(real_rows(rows_by_universe(*rows))))


# -- what is sent -------------------------------------------------------------
def test_only_blocks_spanning_two_shops_are_sent() -> None:
    """A block confined to one shop cannot produce a cross-shop match however
    good the model is, so paying for it would buy nothing."""
    catalogue = rows_by_universe(
        PASTEURISED,
        SALTED,
        row("zepto", "Nandini Butter", brand="Nandini"),
        row("zepto", "Nandini Unsalted Butter", brand="Nandini"),
    )

    blocks = candidate_blocks(assign_ids(real_rows(catalogue)))

    assert list(blocks) == ["amul|500|g"]
    assert {r.universe for _, r in blocks["amul|500|g"]} == {"instamart", "zepto"}


def test_two_pack_sizes_are_two_blocks() -> None:
    """The block is the guard: one call per block makes "never put two pack sizes
    in one group" structurally impossible rather than a line in a prompt."""
    catalogue = rows_by_universe(
        PASTEURISED,
        SALTED,
        row("instamart", "Amul Pasteurised Butter", qty=100.0, price=63.0),
        row("zepto", "Amul Salted Butter", qty=100.0, price=62.0),
    )

    assert list(candidate_blocks(assign_ids(real_rows(catalogue)))) == [
        "amul|100|g",
        "amul|500|g",
    ]


def test_a_kilo_and_a_thousand_grams_are_one_block() -> None:
    catalogue = rows_by_universe(
        row("zepto", "Amul Butter", qty=1.0, unit="kg"),
        row("blinkit", "Amul Butter Pack", qty=1000.0, unit="g"),
    )

    assert list(candidate_blocks(assign_ids(real_rows(catalogue)))) == ["amul|1000|g"]


def test_a_row_with_no_pack_size_can_never_cross_a_shop() -> None:
    """Two listings whose sizes are both unknown are not known to be the same
    size, so the universe goes in the key and the block collapses to one shop."""
    catalogue = rows_by_universe(
        row("zepto", "Amul Butter", qty=None, unit=None),
        row("blinkit", "Amul Butter", qty=None, unit=None),
    )

    assert candidate_blocks(assign_ids(real_rows(catalogue))) == {}
    assert block_key(row("zepto", "Amul Butter", qty=None, unit=None)) == "amul|?|?|zepto"


def test_a_demo_universe_never_reaches_the_model() -> None:
    """`chaos` prices are invented by this app. A model reasoning over them would
    be reasoning about fiction, and its answer would put an invented price in a
    group of real ones."""
    catalogue = rows_by_universe(
        SALTED,
        row("chaos", "Amul Salted Butter", price=299.0),
    )

    assert [r.universe for r in real_rows(catalogue)] == ["zepto"]
    assert candidate_blocks(assign_ids(real_rows(catalogue))) == {}


def test_a_block_renders_one_compact_line_per_row() -> None:
    """Id, name, shop, price. No image urls, store ids, timestamps or ratings:
    none of them says whether two listings are the same product."""
    blocks = candidate_blocks(assign_ids(real_rows(rows_by_universe(PASTEURISED, SALTED))))

    rendered = render_block("amul|500|g", blocks["amul|500|g"])

    assert rendered == (
        "## amul|500|g\n"
        "i0  Amul Pasteurised Butter  [instamart]  Rs310\n"
        "z0  Amul Salted Butter  [zepto]  Rs309"
    )


def test_ids_are_short_and_name_their_shop() -> None:
    pairs = assign_ids(real_rows(rows_by_universe(PASTEURISED, SALTED, row("blinkit", "Amul"))))

    assert [sku for sku, _ in pairs] == ["b0", "i0", "z0"]


# -- the guards ---------------------------------------------------------------
def guarded(cluster: CompactCluster, *rows: NormalizedRow, receipt=frozenset()):
    by_id = by_id_for(*rows)
    return collect_groups([("amul|500|g", cluster)], by_id, receipt)


def test_a_good_group_is_accepted_and_joined_back_from_the_rows() -> None:
    """The model returns ids only. Everything shown comes off the captured rows,
    so it cannot invent a price, a pack size or a product."""
    groups, accepted, rejected = guarded(
        CompactCluster(same=[["i0", "z0"]]), PASTEURISED, SALTED
    )

    assert (accepted, rejected) == (1, 0)
    assert len(groups) == 1
    group = groups[0]
    assert group.source == "model"
    assert group.confidence == "high"
    assert group.universes == ["instamart", "zepto"]
    assert [r.name for r in group.rows] == ["Amul Pasteurised Butter", "Amul Salted Butter"]
    assert [r.price for r in group.rows] == [310.0, 309.0]
    assert (group.brand, group.qty, group.unit) == ("Amul", 500.0, "g")


def test_maybe_is_labelled_low_not_dropped() -> None:
    groups, accepted, _ = guarded(
        CompactCluster(maybe=[["i0", "z0"]]), PASTEURISED, SALTED
    )

    assert accepted == 1
    assert groups[0].confidence == "low"


def test_an_id_nobody_was_given_is_rejected() -> None:
    groups, accepted, rejected = guarded(
        CompactCluster(same=[["i0", "z9"]]), PASTEURISED, SALTED
    )

    assert (groups, accepted, rejected) == ([], 0, 1)


def test_an_id_used_twice_is_rejected_the_second_time() -> None:
    """One listing is one product. A model that put it in two groups has said two
    different things about it, and only the first can be true."""
    third = row("blinkit", "Amul Butter Pasteurised", price=305.0)

    groups, accepted, rejected = guarded(
        CompactCluster(same=[["i0", "z0"], ["b0", "z0"]]), PASTEURISED, SALTED, third
    )

    assert (accepted, rejected) == (1, 1)
    assert [r.universe for r in groups[0].rows] == ["instamart", "zepto"]


def test_a_group_crossing_a_block_is_rejected() -> None:
    """Two pack sizes in one group is the one real error the guards caught in
    testing. Per-block calls make it near-impossible; the guard makes it moot."""
    small = row("blinkit", "Amul Pasteurised Butter", qty=100.0, price=63.0)

    groups, accepted, rejected = guarded(
        CompactCluster(same=[["i0", "z0", "b0"]]), PASTEURISED, SALTED, small
    )

    assert (groups, accepted, rejected) == ([], 0, 1)


def test_an_answer_about_a_different_block_is_rejected() -> None:
    """One call asks about one block, so an answer about another block is an
    answer to a question that was never put - however well formed it looks."""
    other_shop = row("blinkit", "Amul Pasteurised Butter", qty=100.0, price=63.0)
    other_shop_too = row("zepto", "Amul Salted Butter", qty=100.0, price=62.0)

    groups, accepted, rejected = guarded(
        CompactCluster(same=[["b0", "z1"]]), SALTED, other_shop, other_shop_too
    )

    assert (groups, accepted, rejected) == ([], 0, 1)


def test_a_group_inside_one_shop_is_rejected() -> None:
    """Two listings in one shop are two products, not one, and a group of them is
    not a comparison of anything."""
    sibling = row("zepto", "Amul Butter Salted", price=311.0)

    groups, accepted, rejected = guarded(
        CompactCluster(same=[["z0", "z1"]]), SALTED, sibling
    )

    assert (groups, accepted, rejected) == ([], 0, 1)


def test_a_group_the_receipt_already_made_is_counted_but_not_repeated() -> None:
    """`llm_groups` is strictly what the model ADDS. The count still says the
    model got it right, so the two numbers answer two different questions."""
    receipt = {frozenset({("instamart", PASTEURISED.product_id), ("zepto", SALTED.product_id)})}

    groups, accepted, rejected = guarded(
        CompactCluster(same=[["i0", "z0"]]), PASTEURISED, SALTED, receipt=receipt
    )

    assert (groups, accepted, rejected) == ([], 1, 0)


def test_two_model_groups_in_one_block_get_distinct_keys() -> None:
    """The key is what the UI draws a row by, so a collision would silently merge
    two groups into one."""
    other_a = row("instamart", "Amul Unsalted Butter", price=320.0)
    other_b = row("zepto", "Amul White Butter", price=318.0)

    groups, accepted, _ = guarded(
        CompactCluster(same=[["i0", "z0"], ["i1", "z1"]]),
        PASTEURISED,
        other_a,
        SALTED,
        other_b,
    )

    assert accepted == 2
    assert len({g.key for g in groups}) == 2
    assert all(g.key.startswith("amul|500|g#m") for g in groups)


# -- the phase ----------------------------------------------------------------
def store_in(tmp_path: Path) -> EventStore:
    run_id = "r_20260823_120000_ab12"
    return EventStore(run_id, tmp_path / run_id)


def llm_settings(settings, **overrides):
    return dataclasses.replace(
        settings, **{"openrouter_api_key": "not-a-real-key", "llm_timeout_s": 5.0, **overrides}
    )


def statuses(store: EventStore) -> list[str]:
    return [e.data["status"] for e in store.events if e.type == EventType.LLM_MATCH]


async def test_no_key_skips_with_a_reason(settings, tmp_path: Path) -> None:
    store = store_in(tmp_path)
    catalogue = rows_by_universe(PASTEURISED, SALTED)

    outcome = await run_llm_match(store, settings, catalogue, match(catalogue))

    assert outcome.summary.status == "skipped"
    assert outcome.summary.reason == "no key"
    assert statuses(store) == ["skipped"]


async def test_the_layer_can_be_switched_off_with_the_key_left_in_place(
    settings, tmp_path: Path
) -> None:
    off = llm_settings(settings, llm_enabled=False)
    store = store_in(tmp_path)
    catalogue = rows_by_universe(PASTEURISED, SALTED)

    outcome = await run_llm_match(store, off, catalogue, match(catalogue))

    assert (outcome.summary.status, outcome.summary.reason) == ("skipped", "disabled")


async def test_one_shop_alone_is_nothing_to_compare(settings, tmp_path: Path) -> None:
    store = store_in(tmp_path)
    catalogue = rows_by_universe(SALTED)

    outcome = await run_llm_match(store, llm_settings(settings), catalogue, match(catalogue))

    assert (outcome.summary.status, outcome.summary.reason) == ("skipped", "nothing to compare")


async def test_the_phase_reports_what_it_sent_and_what_came_back(
    settings, tmp_path: Path, monkeypatch
) -> None:
    async def answer(client, prompt, model):
        return CompactCluster(same=[["i0", "z0"]]), model

    monkeypatch.setattr(llm_match, "_ask_block", answer)
    store = store_in(tmp_path)
    catalogue = rows_by_universe(PASTEURISED, SALTED)

    outcome = await run_llm_match(store, llm_settings(settings), catalogue, match(catalogue))

    started, done = [e.data for e in store.events if e.type == EventType.LLM_MATCH]
    assert started == {
        "status": "started",
        "model": "stealth/ox-alpha",
        "blocks": 1,
        "rows_sent": 2,
    }
    assert (done["status"], done["accepted"], done["rejected"]) == ("done", 1, 0)
    assert outcome.summary.blocks == 1 and outcome.summary.rows_sent == 2
    assert len(outcome.groups) == 1


async def test_a_block_that_does_not_answer_in_time_is_dropped_and_the_rest_stand(
    settings, tmp_path: Path, monkeypatch
) -> None:
    """A partial answer to an optional question is worth more than nothing. The
    slow block is cancelled, the phase still says `done`, and the receipt beside
    it was never in question."""

    async def one_hangs(client, prompt, model):
        if prompt.startswith("## amul|100|g"):
            await asyncio.sleep(30)
        return CompactCluster(same=[["i0", "z0"]]), model

    monkeypatch.setattr(llm_match, "_ask_block", one_hangs)
    catalogue = rows_by_universe(
        PASTEURISED,
        SALTED,
        row("instamart", "Amul Pasteurised Butter", qty=100.0, price=63.0),
        row("zepto", "Amul Salted Butter", qty=100.0, price=62.0),
    )
    store = store_in(tmp_path)

    outcome = await run_llm_match(
        store, llm_settings(settings, llm_timeout_s=1.0), catalogue, match(catalogue)
    )

    assert statuses(store) == ["started", "done"]
    assert outcome.summary.blocks == 2  # both were asked
    assert outcome.summary.accepted == 1  # one answered
    assert [r.qty for r in outcome.groups[0].rows] == [500.0, 500.0]


async def test_every_block_failing_is_a_failed_phase(
    settings, tmp_path: Path, monkeypatch
) -> None:
    async def refuse(client, prompt, model):
        raise RuntimeError("openrouter said no")

    monkeypatch.setattr(llm_match, "_ask_block", refuse)
    store = store_in(tmp_path)
    catalogue = rows_by_universe(PASTEURISED, SALTED)

    outcome = await run_llm_match(store, llm_settings(settings), catalogue, match(catalogue))

    failed = [e for e in store.events if e.type == EventType.LLM_MATCH][-1]
    assert outcome.summary.status == "failed"
    assert "openrouter said no" in outcome.summary.reason
    assert failed.data["status"] == "failed"
    assert outcome.groups == []


async def test_nothing_answering_in_time_is_a_failure_not_an_empty_success(
    settings, tmp_path: Path, monkeypatch
) -> None:
    async def never(client, prompt, model):
        await asyncio.sleep(30)
        raise AssertionError("unreachable")

    monkeypatch.setattr(llm_match, "_ask_block", never)
    store = store_in(tmp_path)
    catalogue = rows_by_universe(PASTEURISED, SALTED)

    outcome = await run_llm_match(
        store, llm_settings(settings, llm_timeout_s=0.3), catalogue, match(catalogue)
    )

    assert outcome.summary.status == "failed"
    assert "0.3s" in outcome.summary.reason


async def test_the_receipt_is_the_same_object_it_was_before_the_model_ran(
    settings, tmp_path: Path, monkeypatch
) -> None:
    """The one invariant the whole design rests on: this layer is additional."""

    async def answer(client, prompt, model):
        return CompactCluster(same=[["i0", "z0"]]), model

    monkeypatch.setattr(llm_match, "_ask_block", answer)
    catalogue = rows_by_universe(PASTEURISED, SALTED)
    comparison = match(catalogue)
    before = comparison.model_dump_json()

    await run_llm_match(store_in(tmp_path), llm_settings(settings), catalogue, comparison)

    assert comparison.model_dump_json() == before


# -- inside a real run --------------------------------------------------------
ZEPTO_ROWS = [row("zepto", "Amul Salted Butter", price=309.0)]
BLINKIT_ROWS = [row("blinkit", "Amul Pasteurised Butter", price=310.0)]


class FakeBD:
    """A collector client that delivers instantly. The rows come from the mappers
    the test installs, so this only has to get the run to the mapper."""

    mode = "live"

    async def trigger(self, collector_id, inputs, version) -> str:
        return f"j_fake_{inputs[0]['universe']}"

    async def job_log(self, job_id):
        from server.bd_client import JobLog

        return JobLog(job_id=job_id, status="done", navigations=2, lines=1)

    async def fetch_results(self, job_id):
        return [{"universe": job_id.removeprefix("j_fake_")}]

    async def fetch_screenshot(self, record, universe_id):
        return None

    async def cancel_job(self, job_id) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest.fixture
def two_shop_live(settings, monkeypatch):
    """A live run with two shops whose listings the RULES cannot match.

    "Amul Salted Butter" and "Amul Pasteurised Butter" share a brand and a pack
    size but agree on only one word out of three, so the deterministic resolver
    reports two unmatched rows - which is exactly the gap the model layer exists
    to fill.
    """
    monkeypatch.setattr(runs_module, "build_client", lambda s: FakeBD())
    monkeypatch.setitem(mappers.MAPPERS, "zepto", lambda payload: list(ZEPTO_ROWS))
    monkeypatch.setitem(mappers.MAPPERS, "blinkit", lambda payload: list(BLINKIT_ROWS))
    return dataclasses.replace(
        settings,
        bd_mode="live",
        bd_api_key="dummy-test-key",
        bd_base_url="https://api.brightdata.invalid",
        collector_ids={"zepto": "c_test01", "blinkit": "c_test02", "instamart": "", "chaos": ""},
        poll_interval_s=0.0,
        openrouter_api_key="not-a-real-key",
        llm_timeout_s=5.0,
    )


@pytest.fixture
def answering(monkeypatch):
    """The model, stubbed at the one function that reaches the network."""

    async def answer(client, prompt, model):
        return CompactCluster(same=[["b0", "z0"]]), model

    monkeypatch.setattr(llm_match, "_ask_block", answer)


def test_the_layer_runs_between_the_last_universe_and_done(two_shop_live, answering) -> None:
    with TestClient(create_app(two_shop_live)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]
    llm = [e for e in snapshot["events"] if e["type"] == "llm_match"]

    assert types[-3:] == ["llm_match", "llm_match", "done"]
    assert [e["data"]["status"] for e in llm] == ["started", "done"]
    assert all(e["universe"] is None for e in llm)  # a run-level event


def test_the_snapshot_carries_the_layer_beside_an_untouched_receipt(
    two_shop_live, answering
) -> None:
    with TestClient(create_app(two_shop_live)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        comparison = wait_for_done(client, run_id)["comparison"]

    assert comparison["groups"] == []  # the rules cannot join these two names
    assert len(comparison["unmatched"]) == 2
    assert all(g["source"] == "rules" for g in comparison["unmatched"])
    assert len(comparison["llm_groups"]) == 1
    added = comparison["llm_groups"][0]
    assert (added["source"], added["confidence"]) == ("model", "high")
    assert {r["name"] for r in added["rows"]} == {
        "Amul Salted Butter",
        "Amul Pasteurised Butter",
    }
    assert comparison["llm"]["status"] == "done"
    assert comparison["llm"]["model"] == "stealth/ox-alpha"


def test_the_layer_survives_a_restart_and_is_carried_by_a_replay(
    two_shop_live, answering
) -> None:
    """A model answer cannot be re-derived: asking again would mean a second
    answer and a stored run that reads differently every time it is opened."""
    with TestClient(create_app(two_shop_live)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

    stored = json.loads(
        (two_shop_live.runs_dir / run_id / "llm.json").read_text(encoding="utf-8")
    )

    with TestClient(create_app(two_shop_live)) as restarted:
        restarted.cookies.update(client.cookies)
        reopened = restarted.get(f"/api/runs/{run_id}").json()["comparison"]
        replay_id = restarted.post(f"/api/replays/{run_id}").json()["run_id"]
        replayed = wait_for_done(restarted, replay_id)

    assert stored["llm"]["status"] == "done"
    assert len(reopened["llm_groups"]) == 1
    assert reopened["llm"]["accepted"] == 1
    # The replay re-streams the recorded events and carries the capture's layer;
    # it never asks a model of its own.
    assert [e["data"]["status"] for e in replayed["events"] if e["type"] == "llm_match"] == [
        "started",
        "done",
    ]
    assert replayed["comparison"]["llm_groups"] == reopened["llm_groups"]


def test_a_mock_run_says_nothing_about_a_layer_that_never_runs(settings) -> None:
    """Silence, not `skipped`: an event about a layer that was never going to run
    is noise in a feed people read."""
    with TestClient(create_app(dataclasses.replace(settings, openrouter_api_key="x"))) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    assert "llm_match" not in [e["type"] for e in snapshot["events"]]
    assert snapshot["comparison"]["llm"] is None
    assert snapshot["comparison"]["llm_groups"] == []
