"""Blinkit mapper coverage for flattened rows and saved response fixtures."""

from __future__ import annotations

import json

import pytest

from conftest import FIXTURES, make_settings
from server.mappers import get_mapper
from server.mappers.blinkit import map_blinkit
from server.mappers.collector_rows import is_collector_row
from server.mappers.zepto import map_zepto
from server.registry import universes
from server.resolve import NormalizedRow, match
from server.runs import known_store, resolves_pincode, store_ids_in, validate_rows

EXPRESS_STORE = "900001"
LONGTAIL_STORE = "900002"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def search_rows() -> list[NormalizedRow]:
    return map_blinkit(load("blinkit_search_response.json"))


@pytest.fixture
def oos_rows() -> list[NormalizedRow]:
    return map_blinkit(load("blinkit_search_response_oos.json"))


@pytest.fixture
def dataset() -> list[dict]:
    return load("blinkit_collector_rows.json")


def by_name(rows: list[NormalizedRow], name: str) -> NormalizedRow:
    return next(r for r in rows if r.name == name)


# -- raw search response ------------------------------------------------------
def test_every_product_card_becomes_a_row(search_rows: list[NormalizedRow]) -> None:
    """12 product cards. The heading and the "Similar brands" carousel sitting in
    the same `snippets[]` list are not products and must not become rows."""
    assert len(search_rows) == 12
    assert {r.universe for r in search_rows} == {"blinkit"}


def test_prices_are_rupees_not_paise(search_rows: list[NormalizedRow]) -> None:
    """`"₹63"` is sixty-three rupees. Dividing by 100 is a Zepto-only quirk."""
    salted = by_name(search_rows, "Amul Salted Butter")

    assert salted.price == 63.0
    assert salted.qty == 100.0 and salted.unit == "g"
    assert salted.variant == "salted"
    assert (salted.unit_price, salted.unit_price_basis) == (63.0, "per 100 g")
    assert salted.in_stock is True and salted.qty_available == 4


def test_a_discounted_card_reports_both_prices(search_rows: list[NormalizedRow]) -> None:
    milky = by_name(search_rows, "Milky Mist Chiplet Salted Butter")

    assert (milky.price, milky.mrp) == (88.0, 90.0)


def test_mrp_comes_from_the_cart_item_when_the_card_has_none(
    search_rows: list[NormalizedRow],
) -> None:
    """An undiscounted card carries no `mrp` node at all, but the site's own
    `cart_item.mrp` equals the selling price. We report that, and never invent
    an MRP the payload does not state."""
    body = load("blinkit_search_response.json")[0]["search_response"]
    card = next(
        s["data"]
        for s in body["response"]["snippets"]
        if (s.get("data") or {}).get("name", {}).get("text") == "Amul Salted Butter"
    )

    assert "mrp" not in card
    assert card["atc_action"]["add_to_cart"]["cart_item"]["mrp"] == 63

    assert by_name(search_rows, "Amul Salted Butter").mrp == 63.0


def test_the_rank_one_sponsored_substitute_is_kept_and_marked(
    search_rows: list[NormalizedRow],
) -> None:
    """The first card is a paid substitute for the searched product. Dropping it
    would hide what the shopper actually sees; resolution de-emphasises it."""
    first = search_rows[0]

    assert first.name.startswith("Nutralite")
    assert first.sponsored is True
    assert [r.sponsored for r in search_rows].count(True) == 2
    assert by_name(search_rows, "Amul Salted Butter").sponsored is False


def test_eta_is_read_per_card(search_rows: list[NormalizedRow]) -> None:
    """Blinkit stamps the ETA on the card, unlike Zepto's page-level one."""
    assert by_name(search_rows, "Amul Salted Butter").eta_min == 15


def test_a_card_without_an_eta_badge_falls_back_to_the_page_eta() -> None:
    payload = [
        {
            "universe": "blinkit",
            "eta_minutes": 8,
            "search_response": {
                "is_success": True,
                "response": {
                    "snippets": [
                        {
                            "widget_type": "product_card_snippet_type_2",
                            "data": {
                                "name": {"text": "Amul Salted Butter"},
                                "variant": {"text": "100 g"},
                                "normal_price": {"text": "₹63"},
                                "inventory": 4,
                            },
                        }
                    ]
                },
            },
        }
    ]

    assert map_blinkit(payload)[0].eta_min == 8


def test_page_level_provenance_rides_on_every_row(search_rows: list[NormalizedRow]) -> None:
    assert all(r.resolved_area and "560001" in r.resolved_area for r in search_rows)
    assert all(r.captured_at == "2026-08-22T09:12:33.412Z" for r in search_rows)
    assert all(r.product_id and r.image_url for r in search_rows)


def test_rows_point_back_at_the_snippet_they_came_from(
    search_rows: list[NormalizedRow],
) -> None:
    assert search_rows[0].raw_ref == "snippets[1]"  # [0] is the heading


# -- out of stock -------------------------------------------------------------
def test_out_of_stock_cards_are_kept_as_rows(oos_rows: list[NormalizedRow]) -> None:
    """`product_state: out_of_stock` with `inventory: 0` — and `is_sold_out`
    false on every one of them, so that flag is never trusted alone."""
    out = [r for r in oos_rows if not r.in_stock]

    assert len(out) == 3
    assert {r.name for r in out} == {
        "Amul Garlic & Herbs Butter",
        "Amul Lite Milk Fat Spread",
        "Amul Masti Spiced Salted Buttermilk",
    }
    assert all(r.qty_available == 0 for r in out)
    # They still carry a price, so the validation gate keeps them and the
    # zero-rows taxonomy can tell "out of stock" from "collector broke".
    kept, dropped, _ = validate_rows(out)
    assert len(kept) == 3 and dropped == 0


def test_a_second_merchant_in_one_response_still_yields_rows(
    oos_rows: list[NormalizedRow],
) -> None:
    """Blinkit fulfils some items from a longtail warehouse. Those cards are real
    rows with their own ETA, not something to filter out."""
    longtail = [r for r in oos_rows if r.name.startswith("Jus'Amazin")]

    assert len(longtail) == 2
    assert all(r.eta_min == 30 for r in longtail)


# -- collector rows -----------------------------------------------------------
def test_collector_rows_are_detected_and_mapped(dataset) -> None:
    assert all(is_collector_row(record) for record in dataset)

    rows = map_blinkit(dataset)

    assert len(rows) == 6
    assert {r.universe for r in rows} == {"blinkit"}
    assert by_name(rows, "Amul Salted Butter").price == 63.0  # rupees, not 0.63


def test_collector_rows_carry_stock_sponsorship_and_fallbacks(dataset) -> None:
    rows = map_blinkit(dataset)

    oos = by_name(rows, "Amul Garlic & Herbs Butter")
    assert oos.in_stock is False and oos.qty_available == 0
    assert oos.eta_min == 8  # own eta null -> the dataset's

    assert by_name(rows, "Nutralite DoodhShakti Probiotic Salted Butter").sponsored is True
    # discounted_selling_price null falls back to selling_price
    assert by_name(rows, "Milky Mist Chiplet Salted Butter").price == 88.0


def test_junk_payloads_yield_no_rows_rather_than_raising() -> None:
    assert map_blinkit({}) == []
    assert map_blinkit([]) == []
    assert map_blinkit("not a payload") == []
    assert map_blinkit({"is_success": False, "response": {"snippets": []}}) == []
    assert map_blinkit({"response": {"snippets": [{"widget_type": "banner"}]}}) == []


# -- location proof, per universe ---------------------------------------------
def test_blinkits_resolved_line_carries_the_pincode(search_rows, dataset) -> None:
    """Both fixture shapes carry the requested pincode."""
    collector_rows = map_blinkit(dataset)

    for row in list(search_rows) + list(collector_rows):
        assert resolves_pincode(row, "560001") is True
        assert resolves_pincode(row, "110001") is False


def test_both_of_blinkits_stores_are_recorded(dataset) -> None:
    """One search answered from an express dark store AND a longtail warehouse.
    Both ids are reported; neither refuses anything."""
    assert store_ids_in(dataset) == sorted({EXPRESS_STORE, LONGTAIL_STORE})

    assert known_store(store_ids_in(dataset), "560001", {"560001": {EXPRESS_STORE, LONGTAIL_STORE}}) is True
    # A map listing only the express store no longer refuses the universe; it
    # just says the longtail warehouse is not one we have written down.
    assert known_store(store_ids_in(dataset), "560001", {"560001": {EXPRESS_STORE}}) is False
    assert known_store(store_ids_in(dataset), "560001", {}) is None


def test_a_blinkit_merchant_is_never_judged_against_the_zepto_store() -> None:
    """The bug the per-universe maps fix: one shared map flagged every universe
    whose ids were not Zepto UUIDs."""
    settings = make_settings(
        FIXTURES.parent / "runs",
        store_maps={"zepto": {"560001": frozenset({"a-zepto-uuid"})}},
    )
    ids = [EXPRESS_STORE]

    # Blinkit has no map of its own, so there is nothing to compare against.
    assert known_store(ids, "560001", settings.store_map_for("blinkit")) is None
    assert known_store(ids, "560001", settings.store_map_for("zepto")) is False


# -- wiring -------------------------------------------------------------------
def test_blinkit_is_dispatchable_in_live_mode_once_its_collector_id_is_set(tmp_path) -> None:
    settings = make_settings(
        tmp_path / "runs",
        bd_mode="live",
        collector_ids={
            "zepto": "",
            "blinkit": "c_fixture_blinkit",
            "instamart": "c_fixture_instamart",
            "chaos": "",
        },
    )

    rows = {u.id: u for u in universes(settings)}

    assert (rows["blinkit"].wired, rows["blinkit"].dispatchable) == (True, True)
    assert rows["blinkit"].status == "wired"
    assert rows["blinkit"].collector_id == ""  # never leaves the process
    assert rows["zepto"].wired is False and rows["zepto"].status == "not wired"
    # A collector id is not enough on its own. Instamart has a real mapper now,
    # so the pair that still proves it is chaos: no id AND no mapper.
    assert rows["instamart"].wired is True
    assert (rows["instamart"].dispatchable, rows["instamart"].status) == (True, "wired")
    assert (rows["chaos"].dispatchable, rows["chaos"].status) == (False, "not wired")


# -- cross-universe comparison ------------------------------------------------
def test_zepto_and_blinkit_fixtures_produce_a_matched_group() -> None:
    """The fixtures share one product and keep different pack sizes separate."""
    rows = {
        "zepto": map_zepto(load("zepto_collector_result.json")),
        "blinkit": map_blinkit(load("blinkit_search_response.json")),
    }

    comparison = match(rows)
    matched = {g.key: g for g in comparison.groups}

    assert "amul|100|g|unsalted#0" in matched
    group = matched["amul|100|g|unsalted#0"]
    assert group.confidence == "close"
    assert group.universes == ["blinkit", "zepto"]
    assert {r.universe: r.price for r in group.rows} == {"zepto": 65.0, "blinkit": 65.0}
    assert comparison.universe_count == 2

    # Nothing was matched across pack sizes.
    assert all(len({r.qty for r in g.rows}) == 1 for g in comparison.groups)


def test_the_registered_blinkit_mapper_is_the_real_one() -> None:
    assert get_mapper("blinkit") is map_blinkit
