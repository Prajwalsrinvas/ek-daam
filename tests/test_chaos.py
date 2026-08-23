"""The chaos universe: the store this app serves, its version flip, and the
mapper that reads its collector output.

Nothing here touches the network. The self-heal tests drive a stand-in for the
Bright Data client, so what is under test is the orchestration and the events it
emits, not Bright Data.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import FIXTURES, make_settings, wait_for_done
from server.app import create_app
from server.chaos_store import (
    CATALOG,
    VERSIONS,
    ChaosStore,
    catalog_rows,
    matching,
    render_page,
    render_product_page,
    resolved_area_for,
    search,
    store_id_for,
    tile_for_image,
)
from server.resolve import NormalizedRow, match
from server.mappers import get_mapper
from server.mappers.chaos import map_chaos
from server.registry import universes
from server.runs import RunRejected, RunThrottled, RunManager, resolves_pincode

TOKEN = "test-chaos-token"
PINCODE = "560001"


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path / "runs", chaos_admin_token=TOKEN)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# -- the store page -----------------------------------------------------------
_V1_CARD = re.compile(r'<article class="product-card".*?</article>', re.S)
_V2_ROW = re.compile(r'<tr class="listing-row".*?</tr>', re.S)


def _one(pattern: str, block: str) -> str | None:
    found = re.search(pattern, block, re.S)
    return found.group(1) if found else None


def _blocks(version: str, page: str) -> list[str]:
    """The listing blocks only. The stylesheet mentions `data-match` too, so a
    whole-page count would score the CSS as a product."""
    return (_V1_CARD if version == "v1" else _V2_ROW).findall(page)


def _rows_v1(page: str) -> list[tuple]:
    out = []
    for block in _V1_CARD.findall(page):
        out.append(
            (
                _one(r'data-product-id="([^"]+)"', block),
                _one(r'<h3 class="product-title">([^<]*)</h3>', block),
                _one(r'<p class="product-brand">([^<]*)</p>', block),
                _one(r'<p class="pack-size">([^<]*)</p>', block),
                _one(r'<span class="price-now">₹(\d+)</span>', block),
                _one(r'<span class="price-mrp">₹(\d+)</span>', block),
                _one(r'data-in-stock="([^"]+)"', block) == "true",
                'class="sponsored-tag"' in block,
            )
        )
    return out


def _rows_v2(page: str) -> list[tuple]:
    out = []
    for block in _V2_ROW.findall(page):
        out.append(
            (
                _one(r'data-sku="([^"]+)"', block),
                _one(r'<span class="item-label">([^<]*)</span>', block),
                _one(r'<span class="item-maker">([^<]*)</span>', block),
                _one(r'<span class="item-measure">([^<]*)</span>', block),
                _one(r'<span class="cost-value" data-rupees="(\d+)"', block),
                _one(r'<span class="cost-list" data-rupees="(\d+)"', block),
                _one(r'data-stock="([^"]+)"', block) == "in",
                'class="flag-promoted"' in block,
            )
        )
    return out


def test_both_versions_serve_the_same_products_in_different_markup() -> None:
    """The point of the second version: same facts, different DOM. A price that
    moved between renderings would make a repaired collector impossible to tell
    apart from a broken one."""
    v1 = render_page("v1", "butter", PINCODE)
    v2 = render_page("v2", "butter", PINCODE)

    assert _rows_v1(v1) == _rows_v2(v2)
    assert len(_rows_v1(v1)) == len(search("butter"))
    # Structurally different, which is what breaks a collector written for v1.
    assert 'class="product-card"' in v1 and 'class="product-card"' not in v2
    assert 'class="listing-row"' in v2 and 'class="listing-row"' not in v1
    assert 'id="delivery-area"' in v1 and 'id="serviceArea"' in v2


def test_no_machine_readable_copy_of_the_catalogue_is_served() -> None:
    """A parser that could read a JSON blob out of the page would survive any
    redesign, so the break, and therefore the repair, would prove nothing."""
    for version in VERSIONS:
        page = render_page(version, "", PINCODE)
        assert "application/json" not in page
        assert "selling_price" not in page and "product_id" not in page


@pytest.mark.parametrize("version", VERSIONS)
def test_the_page_echoes_the_requested_pincode(version: str) -> None:
    page = render_page(version, "amul butter", PINCODE)

    assert resolved_area_for(PINCODE) in page
    assert store_id_for(PINCODE) in page


@pytest.mark.parametrize("version", VERSIONS)
def test_a_missing_pincode_shows_no_prices(version: str) -> None:
    """The store behaves like a real one: no location, no shelf. Serving a
    default location instead would hand a collector a location proof it never
    asked for."""
    page = render_page(version, "amul butter", "")

    assert "Enter a 6-digit pincode" in page
    assert _rows_v1(page) == [] and _rows_v2(page) == []


@pytest.mark.parametrize("version", VERSIONS)
def test_a_malformed_pincode_is_treated_as_no_location(version: str) -> None:
    for bad in ("56000", "abcdef", "0560001", " "):
        assert "Enter a 6-digit pincode" in render_page(version, "", bad)


@pytest.mark.parametrize("version", VERSIONS)
def test_the_whole_shelf_renders_whatever_was_searched_for(version: str) -> None:
    """A store that hid everything it was not asked about would make the demo
    depend on typing the one keyword the catalogue was built around. Every
    product renders for every query; the query orders the shelf instead."""
    rows = _rows_v1 if version == "v1" else _rows_v2

    for query in ("", "amul butter", "zzzz"):
        assert len(rows(render_page(version, query, PINCODE))) == len(CATALOG)


@pytest.mark.parametrize("version", VERSIONS)
def test_the_query_orders_and_marks_the_shelf_rather_than_cutting_it(version: str) -> None:
    page = render_page(version, "amul butter", PINCODE)
    rows = _rows_v1 if version == "v1" else _rows_v2
    hits = matching("amul butter")

    # The matches lead, in catalogue order, and the rest of the shelf follows.
    assert [row[0] for row in rows(page)][: len(hits)] == [p.product_id for p in hits]
    assert len(hits) < len(CATALOG)
    marked = [b for b in _blocks(version, page) if 'data-match="true"' in b]
    assert len(marked) == len(hits)
    assert len(_blocks(version, page)) - len(marked) == len(CATALOG) - len(hits)


@pytest.mark.parametrize("version", VERSIONS)
def test_a_search_that_matches_nothing_says_so_and_still_shows_the_shelf(version: str) -> None:
    page = render_page(version, "zzzz", PINCODE)
    rows = _rows_v1 if version == "v1" else _rows_v2

    assert "No products matched" in page
    assert not any('data-match="true"' in b for b in _blocks(version, page))
    assert len(rows(page)) == len(CATALOG)


def test_the_store_route_serves_the_active_version(client: TestClient) -> None:
    page = client.get("/chaos", params={"q": "amul butter", "pincode": PINCODE})

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert 'data-store-version="v1"' in page.text
    # /chaos/search is the same page under a more site-like address.
    assert client.get("/chaos/search", params={"pincode": PINCODE}).status_code == 200


@pytest.mark.parametrize("version", VERSIONS)
def test_every_listing_links_to_its_own_product_page(version: str) -> None:
    page = render_page(version, "", PINCODE)

    for product in CATALOG:
        assert f'href="/chaos/product/{product.product_id}?pincode={PINCODE}"' in page


def test_the_product_page_shows_one_product_and_refuses_an_unknown_id() -> None:
    page = render_product_page("cm-1016", PINCODE)

    assert page is not None
    assert "Surf Excel Easy Wash Detergent Powder" in page
    assert "₹132" in page and "₹145" in page
    assert resolved_area_for(PINCODE) in page
    assert render_product_page("cm-9999", PINCODE) is None


def test_the_product_page_shows_no_price_without_a_pincode() -> None:
    """The same rule the shelf follows: a price with no delivery area behind it
    is not a price anybody can act on."""
    page = render_product_page("cm-1016", "")

    assert page is not None
    assert "Enter a 6-digit pincode" in page
    assert "₹132" not in page


def test_the_product_route_serves_the_page_and_404s_an_unknown_id(client: TestClient) -> None:
    found = client.get("/chaos/product/cm-1001", params={"pincode": PINCODE})

    assert found.status_code == 200
    assert "Amul Butter Salted" in found.text
    assert client.get("/chaos/product/cm-9999").status_code == 404


def test_every_product_thumbnail_is_its_own_tile(client: TestClient) -> None:
    """The store is fictional and has no photography, so a tile carries a colour
    and the product's initials. Identical tiles would make a shelf unreadable."""
    names = {p.image_url.rsplit("/", 1)[-1] for p in CATALOG if p.image_url}
    served = {name: client.get(f"/chaos/static/{name}") for name in names}

    assert all(r.status_code == 200 for r in served.values())
    assert all(r.headers["content-type"] == "image/png" for r in served.values())
    # Not all 22 differ - the palette is smaller than the catalogue - but a tile
    # is never the same as its neighbour's, which is what a shelf needs.
    assert len({r.content for r in served.values()}) > len(names) // 2
    assert tile_for_image("surf-excel-1kg.png") == ((161, 92, 0), "SE")


def test_the_state_endpoint_reports_the_active_version(client: TestClient) -> None:
    body = client.get("/api/chaos").json()

    assert body["version"] == "v1"
    assert body["versions"] == list(VERSIONS)
    assert body["products"] == len(CATALOG)
    assert body["admin_enabled"] is True


# -- the flip -----------------------------------------------------------------
def test_flipping_requires_the_token(client: TestClient) -> None:
    assert client.post("/api/chaos/flip", json={}).status_code == 401
    assert (
        client.post("/api/chaos/flip", json={}, headers={"X-Chaos-Token": "wrong"}).status_code
        == 401
    )
    # Refused means unchanged: the store is still serving what it was.
    assert client.get("/api/chaos").json()["version"] == "v1"


def test_flipping_is_disabled_when_no_token_is_configured(tmp_path) -> None:
    """An empty token is not a blank password. There is no value a caller could
    send that would be accepted."""
    with TestClient(create_app(make_settings(tmp_path / "runs"))) as anon:
        refused = anon.post("/api/chaos/flip", json={}, headers={"X-Chaos-Token": ""})

        assert refused.status_code == 503
        assert anon.get("/api/chaos").json()["admin_enabled"] is False


def test_the_token_holder_can_flip_the_store(client: TestClient) -> None:
    flipped = client.post(
        "/api/chaos/flip", json={"version": "v2"}, headers={"X-Chaos-Token": TOKEN}
    )

    assert flipped.status_code == 200
    assert flipped.json() == {"previous": "v1", "version": "v2", "versions": list(VERSIONS)}
    assert client.get("/api/chaos").json()["version"] == "v2"

    page = client.get("/chaos", params={"pincode": PINCODE}).text
    assert 'data-layout="v2"' in page and 'data-store-version="v1"' not in page


def test_flipping_with_no_version_advances_to_the_next_one(client: TestClient) -> None:
    first = client.post("/api/chaos/flip", json={}, headers={"X-Chaos-Token": TOKEN}).json()
    second = client.post("/api/chaos/flip", json={}, headers={"X-Chaos-Token": TOKEN}).json()

    assert first["version"] == "v2"
    assert second["version"] == "v1"  # wraps, so the demo can be run twice


def test_an_unknown_version_is_refused(client: TestClient) -> None:
    refused = client.post(
        "/api/chaos/flip", json={"version": "v9"}, headers={"X-Chaos-Token": TOKEN}
    )

    assert refused.status_code == 400
    assert client.get("/api/chaos").json()["version"] == "v1"


def test_the_store_object_refuses_an_unknown_version() -> None:
    store = ChaosStore("v2")

    assert store.version == "v2"
    with pytest.raises(ValueError):
        store.flip("v3")
    assert store.version == "v2"


def test_an_unrecognised_configured_version_falls_back(tmp_path) -> None:
    assert ChaosStore("nonsense").version == "v1"


# -- the mapper ---------------------------------------------------------------
def collector_row(**overrides: Any) -> dict[str, Any]:
    """One row in the 18-field contract, as the chaos collector delivers it."""
    row = {
        "product_name": "Amul Butter Salted",
        "brand": "Amul",
        "package_size": "100 g",
        "product_id": "cm-1001",
        "mrp": 64,
        "selling_price": 61,
        "discounted_selling_price": None,
        "out_of_stock": False,
        "available_quantity": 18,
        "is_sponsored": False,
        "rating": 4.6,
        "image_url": "/chaos/static/butter-100.png",
        "serp_screenshot": None,
        "store_id": store_id_for(PINCODE),
        "requested_pincode": PINCODE,
        "resolved_area": resolved_area_for(PINCODE),
        "eta_minutes": 11,
        "captured_at": "2026-08-23T13:45:00.000Z",
    }
    row.update(overrides)
    return row


def test_the_chaos_mapper_reads_the_shared_row_contract() -> None:
    rows = map_chaos([collector_row()])

    assert len(rows) == 1
    row = rows[0]
    assert (row.universe, row.name, row.brand) == ("chaos", "Amul Butter Salted", "Amul")
    assert (row.qty, row.unit) == (100.0, "g")
    assert (row.price, row.mrp) == (61.0, 64.0)
    assert (row.unit_price, row.unit_price_basis) == (61.0, "per 100 g")
    assert (row.variant, row.in_stock, row.qty_available) == ("salted", True, 18)
    assert row.eta_min == 11 and row.sponsored is False
    assert row.resolved_area == resolved_area_for(PINCODE)


def test_the_chaos_mapper_handles_the_shelf_cases() -> None:
    rows = map_chaos(
        [
            collector_row(product_id="cm-1002", is_sponsored=True, package_size="500 g"),
            collector_row(product_id="cm-1004", package_size="100 g X 2", selling_price=118),
            collector_row(product_id="cm-1005", out_of_stock=True, available_quantity=0),
            collector_row(product_id="cm-1003", product_name="Amul Unsalted Butter"),
        ]
    )

    by_id = {row.product_id: row for row in rows}

    assert by_id["cm-1002"].sponsored is True  # kept and flagged, never dropped
    assert (by_id["cm-1004"].qty, by_id["cm-1004"].unit) == (200.0, "g")  # multipack total
    assert by_id["cm-1005"].in_stock is False
    assert by_id["cm-1003"].variant == "unsalted"


def test_the_chaos_mapper_reports_nothing_for_a_shape_it_cannot_read() -> None:
    """A collector broken by a redesign delivers something that is not a row.
    Reporting no rows is what makes `runs.py` say `zero_rows{broken}`."""
    assert map_chaos([]) == []
    assert map_chaos({"html": "<div>the page</div>"}) == []
    assert map_chaos("not a payload") == []
    assert get_mapper("chaos") is map_chaos


def test_a_chaos_row_proves_its_location_the_same_way_every_other_row_does() -> None:
    row = map_chaos([collector_row()])[0]
    elsewhere = map_chaos([collector_row(resolved_area=resolved_area_for("110001"))])[0]

    assert resolves_pincode(row, PINCODE) is True
    assert resolves_pincode(elsewhere, PINCODE) is False


# -- shown, never compared ----------------------------------------------------
def test_a_chaos_row_never_joins_a_cross_universe_group() -> None:
    """The chaos store's prices are invented. Putting one in the same row as
    three real ones would make the comparison say something untrue, however
    honestly the row were labelled - so the row is shown on its own instead."""
    zepto = NormalizedRow(
        universe="zepto", name="Amul Salted Butter", brand="Amul",
        variant="salted", qty=100.0, unit="g", price=62.0,
    )
    blinkit = NormalizedRow(
        universe="blinkit", name="Amul Butter (Salted)", brand="Amul",
        variant="salted", qty=100.0, unit="g", price=61.0,
    )
    # Same brand, same pack, same variant, agreeing name: this row would have
    # joined the group above if it were allowed anywhere near it.
    chaos = NormalizedRow(
        universe="chaos", name="Amul Butter Salted", brand="Amul", variant="salted",
        qty=100.0, unit="g", price=61.0, product_id="cm-1001",
    )

    comparison = match({"zepto": [zepto], "blinkit": [blinkit], "chaos": [chaos]})
    compared = comparison.groups + comparison.unmatched

    assert len(comparison.groups) == 1
    assert comparison.groups[0].universes == ["blinkit", "zepto"]
    assert "chaos" not in {row.universe for group in compared for row in group.rows}
    assert [row.product_id for row in comparison.demo_rows] == ["cm-1001"]
    # The comparison's own numbers describe the real universes only.
    assert comparison.row_count == 2
    assert comparison.universe_count == 2


def test_a_chaos_only_run_produces_rows_to_show_and_nothing_to_compare() -> None:
    chaos = NormalizedRow(
        universe="chaos", name="Amul Butter Salted", brand="Amul", variant="salted",
        qty=100.0, unit="g", price=61.0, product_id="cm-1001",
    )

    comparison = match({"chaos": [chaos]})

    assert comparison.groups == [] and comparison.unmatched == []
    assert comparison.demo_rows == [chaos]
    assert (comparison.row_count, comparison.universe_count) == (0, 0)


def test_the_snapshot_and_done_event_keep_the_chaos_rows_out_of_the_comparison(
    tmp_path,
) -> None:
    """End to end through the mock path: chaos really collects, really validates,
    and its rows arrive in `demo_rows` rather than in a comparison group."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "zepto_collector_result.json").write_text(
        (FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (fixtures / "chaos_collector_result.json").write_text(
        json.dumps(catalog_rows("amul butter", PINCODE)), encoding="utf-8"
    )
    settings = make_settings(tmp_path / "runs", fixtures_dir=fixtures)

    with TestClient(create_app(settings)) as local:
        run_id = local.post(
            "/api/runs", json={"query": "amul butter", "pincode": PINCODE}
        ).json()["run_id"]
        snapshot = wait_for_done(local, run_id)

    comparison = snapshot["comparison"]
    done = next(e for e in snapshot["events"] if e["type"] == "done")
    validated = {e["universe"] for e in snapshot["events"] if e["type"] == "validated"}
    compared = comparison["groups"] + comparison["unmatched"]

    # It ran, and it stood behind its rows, exactly like a real universe.
    assert "chaos" in validated
    assert len(comparison["demo_rows"]) == len(CATALOG)
    # Built server-side from the product id, and it really is the page this app
    # serves - the one universe whose pattern is certain rather than inferred.
    assert comparison["demo_rows"][0]["product_url"] == "/chaos/product/cm-1001"
    assert "chaos" not in {r["universe"] for g in compared for r in g["rows"]}
    # The done event's numbers are the comparison's numbers: real universes only.
    assert done["data"]["demo_rows"] == len(CATALOG)
    assert done["data"]["rows_total"] == comparison["row_count"]
    assert done["data"]["groups"] == len(comparison["groups"])


# -- dispatch gating ----------------------------------------------------------
def test_chaos_takes_no_part_in_a_run_until_its_collector_is_wired(tmp_path) -> None:
    unwired = make_settings(
        tmp_path / "runs",
        bd_mode="live",
        collector_ids={"zepto": "c_zepto", "blinkit": "", "instamart": "", "chaos": ""},
    )
    wired = make_settings(
        tmp_path / "runs",
        bd_mode="live",
        collector_ids={"zepto": "c_zepto", "blinkit": "", "instamart": "", "chaos": "c_chaos"},
    )

    off = {u.id: u for u in universes(unwired)}["chaos"]
    on = {u.id: u for u in universes(wired)}["chaos"]

    assert (off.wired, off.dispatchable, off.status) == (False, False, "not wired")
    assert (on.wired, on.dispatchable, on.status) == (True, True, "wired")
    assert on.collector_id == ""  # never leaves the process
    assert on.badge == "chaos"


def test_chaos_is_not_dispatched_in_mock_mode_without_a_fixture(client: TestClient) -> None:
    created = client.post("/api/runs", json={"query": "amul butter", "pincode": PINCODE})
    snapshot = wait_for_done(client, created.json()["run_id"])

    assert "chaos" not in {e["universe"] for e in snapshot["events"] if e["universe"]}
    assert snapshot["meta"]["universes"] == ["zepto"]


# -- self-heal ----------------------------------------------------------------
class FakeHealClient:
    """Stands in for Bright Data. Serves a scripted sequence of progress
    payloads and records what it was asked to do."""

    def __init__(self, progress: list[dict[str, Any]], resume: dict[str, Any] | None = None) -> None:
        self._progress = list(progress)
        self._resume = resume or {}
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    async def refactor_template(self, collector_id, prompt, custom_input):
        self.calls.append(("refactor_template", {"prompt": prompt, "custom_input": custom_input}))
        return {"status": "running"}

    async def progress(self, collector_id):
        payload = self._progress[0] if len(self._progress) == 1 else self._progress.pop(0)
        self.calls.append(("progress", payload.get("status")))
        return payload

    async def resume(self, collector_id, message=True, auto_save=True):
        self.calls.append(("resume", {"message": message, "auto_save": auto_save}))
        return self._resume

    async def aclose(self):
        self.closed = True


def heal_settings(tmp_path, **overrides):
    base = {
        "bd_mode": "live",
        "bd_api_key": "dummy",
        "collector_ids": {"zepto": "", "blinkit": "", "instamart": "", "chaos": "c_chaos"},
        "chaos_admin_token": TOKEN,
        "heal_poll_interval_s": 0.0,
        "heal_timeout_s": 5.0,
    }
    return make_settings(tmp_path / "runs", **{**base, **overrides})


async def drive_heal(manager: RunManager, fake: FakeHealClient, **kwargs):
    meta = manager.start_heal(client=fake, **kwargs)
    await manager._heal_tasks[meta.run_id]
    return meta, [e.model_dump() for e in manager.events_for(meta.run_id)]


def test_a_full_heal_cycle_emits_only_what_bright_data_reported(tmp_path) -> None:
    fake = FakeHealClient(
        progress=[
            {"status": "running", "step": "planner", "completed_steps": ["prepare_intent_analyzer"]},
            {
                "status": "pending_answer",
                "step": "user_approval",
                "completed_steps": ["planner", "code_fixer", "step_preview_runner"],
                "preview_result": {"product_name": "Amul Butter Salted", "selling_price": 61},
            },
            {
                "status": "done",
                "step": "save_new_template",
                "completed_steps": ["user_approval", "save_new_template"],
                "template": "t_chaos.2",
            },
        ],
    )
    manager = RunManager(heal_settings(tmp_path))

    meta, events = asyncio.run(drive_heal(manager, fake, prompt="fix the price selector"))
    types = [e["type"] for e in events]

    assert types == [
        "heal_started",
        "progress",
        "progress",
        "heal_previewed",
        "heal_approved",
        "progress",
        "heal_promoted",
        "done",
    ]
    previewed = next(e for e in events if e["type"] == "heal_previewed")
    promoted = next(e for e in events if e["type"] == "heal_promoted")
    approved = next(e for e in events if e["type"] == "heal_approved")

    # Every value came off a Bright Data payload.
    assert previewed["data"]["preview_result"]["selling_price"] == 61
    assert promoted["data"]["template"] == "t_chaos.2"
    assert "save_new_template" in promoted["data"]["completed_steps"]
    # auto_save is the field that publishes the approved template. Without it the
    # approval leaves an unpublished draft.
    assert approved["data"]["auto_save"] is True
    assert ("resume", {"message": True, "auto_save": True}) in fake.calls
    assert all(e["universe"] in (None, "chaos") for e in events)
    assert meta.status == "done"
    assert fake.closed is True


def test_an_approved_job_that_never_saved_is_reported_as_a_failure(tmp_path) -> None:
    """The trap this project hit for real: approval alone leaves a draft, and the
    only evidence of publication is `save_new_template` in the job's own steps."""
    fake = FakeHealClient(
        progress=[
            {"status": "pending_answer", "step": "user_approval", "preview_result": {"a": 1}},
            {"status": "done", "step": "user_approval", "completed_steps": ["user_approval"]},
        ],
    )
    manager = RunManager(heal_settings(tmp_path))

    meta, events = asyncio.run(drive_heal(manager, fake))
    failures = [e for e in events if e["type"] == "failed"]

    assert [e["type"] for e in events][-2:] == ["failed", "failed"]
    assert "save_new_template" in failures[0]["data"]["error"]
    assert "not published" in failures[0]["data"]["error"]
    assert failures[0]["universe"] == "chaos" and failures[1]["universe"] is None
    assert "heal_promoted" not in {e["type"] for e in events}
    assert meta.status == "failed"


def test_a_bright_data_failure_surfaces_instead_of_a_fake_success(tmp_path) -> None:
    fake = FakeHealClient(progress=[{"status": "failed", "step": "code_fixer"}])
    manager = RunManager(heal_settings(tmp_path))

    meta, events = asyncio.run(drive_heal(manager, fake))

    assert {e["type"] for e in events} == {"heal_started", "progress", "failed"}
    assert "failed" in next(e for e in events if e["type"] == "failed")["data"]["error"]
    assert meta.status == "failed"


def test_a_heal_that_never_reaches_the_approval_gate_is_not_called_a_heal(tmp_path) -> None:
    fake = FakeHealClient(
        progress=[{"status": "done", "step": "collector_maintainer", "completed_steps": []}]
    )
    manager = RunManager(heal_settings(tmp_path))

    _, events = asyncio.run(drive_heal(manager, fake))
    error = next(e for e in events if e["type"] == "failed")["data"]["error"]

    assert "approval gate" in error
    assert ("resume", {"message": True, "auto_save": True}) not in fake.calls


def test_a_second_heal_is_refused_while_one_is_running(tmp_path) -> None:
    fake = FakeHealClient(progress=[{"status": "running", "step": "planner"}])
    manager = RunManager(heal_settings(tmp_path, heal_poll_interval_s=0.05, heal_timeout_s=0.2))

    async def run() -> None:
        manager.start_heal(client=fake)
        with pytest.raises(RunThrottled):
            manager.start_heal(client=FakeHealClient(progress=[{"status": "done"}]))
        await asyncio.sleep(0.4)
        await manager.shutdown()

    asyncio.run(run())


def test_a_heal_is_refused_when_no_collector_is_configured(tmp_path) -> None:
    manager = RunManager(make_settings(tmp_path / "runs", bd_mode="live", bd_api_key="dummy"))

    with pytest.raises(RunRejected):
        manager.start_heal(client=FakeHealClient(progress=[{"status": "done"}]))


def test_an_over_long_heal_prompt_is_refused_before_it_is_sent(tmp_path) -> None:
    manager = RunManager(heal_settings(tmp_path))

    with pytest.raises(RunRejected) as refused:
        manager.start_heal(prompt="x" * 1001, client=FakeHealClient(progress=[]))

    assert "1000" in str(refused.value)


def test_healing_requires_the_token(client: TestClient) -> None:
    assert client.post("/api/chaos/heal", json={}).status_code == 401
    assert (
        client.post("/api/chaos/heal", json={}, headers={"X-Chaos-Token": "wrong"}).status_code
        == 401
    )


def test_healing_with_the_token_but_no_collector_says_so(client: TestClient) -> None:
    refused = client.post("/api/chaos/heal", json={}, headers={"X-Chaos-Token": TOKEN})

    assert refused.status_code == 400
    assert "nothing to heal" in refused.json()["detail"]
