"""Instamart mapper coverage for flattened rows and saved response fixtures."""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from conftest import FIXTURES, make_settings
from server.bd_client import LiveClient
from server.config import build_settings
from server.mappers import get_mapper, screenshot_record
from server.mappers.blinkit import map_blinkit
from server.mappers.collector_rows import is_collector_row
from server.mappers.instamart import map_instamart
from server.mappers.zepto import map_zepto
from server.registry import universes
from server.resolve import NormalizedRow, match, to_base_qty
from server.runs import known_store, resolves_pincode, store_ids_in, validate_rows

# The real pod behind these fixtures lives in SVERSE_INSTAMART_STORE_MAP, never
# in a committed file (DESIGN.md §10).
POD = "930001"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def dataset() -> list[dict]:
    return load("instamart_collector_rows.json")


@pytest.fixture
def rows(dataset) -> list[NormalizedRow]:
    return map_instamart(dataset)


@pytest.fixture
def search_rows() -> list[NormalizedRow]:
    return map_instamart(load("instamart_search_response.json"))


def by_name(rows: list[NormalizedRow], name: str) -> list[NormalizedRow]:
    return [r for r in rows if r.name == name]


def one(rows: list[NormalizedRow], name: str, qty: float) -> NormalizedRow:
    return next(r for r in by_name(rows, name) if r.qty == qty)


# -- collector rows (the live path) -------------------------------------------
def test_every_collector_row_becomes_a_row(dataset, rows) -> None:
    """75 rows in, 75 rows out. Instamart lists one row per PACK, so the same
    product legitimately appears several times at different sizes."""
    assert len(dataset) == 75
    assert all(is_collector_row(record) for record in dataset)
    assert len(rows) == 75
    assert {r.universe for r in rows} == {"instamart"}


def test_prices_are_rupees_and_the_shelf_price_is_reported(rows) -> None:
    butter = one(rows, "Amul Pasteurised Butter", 100.0)

    assert butter.price == 63.0  # rupees, not paise
    assert butter.unit == "g"
    assert (butter.unit_price, butter.unit_price_basis) == (63.0, "per 100 g")


def test_a_multipack_resolves_to_the_total_weight(rows) -> None:
    """`"100 g x 4"` is 400 g. The site itself says so three ways: its
    `weightInGrams` is 400, its `unitLevelPrice` is "63/100 g", and the pack
    costs exactly 4x the single (252 vs 63). Reading it as 100 g would report a
    unit price four times too high."""
    four_pack = one(rows, "Amul Pasteurised Butter", 400.0)
    single = one(rows, "Amul Pasteurised Butter", 100.0)

    assert four_pack.price == 252.0 == single.price * 4
    assert (four_pack.qty, four_pack.unit) == (400.0, "g")
    # Same shelf, same value per gram — which is the point of the total.
    assert four_pack.unit_price == single.unit_price == 63.0


def test_a_combo_has_no_comparable_pack_size(dataset, rows) -> None:
    """A butter-and-bread bundle is `"1 Combo"`. It has no quantity we could
    compare per-100 g, so it gets none — and it still appears as a real row
    rather than being dropped from the shelf we claim to be reporting.

    The raw payload does offer a number here (`weightInGrams` says 520 g for the
    butter-and-noodles bundle), and taking it would have invented a per-100 g
    price for a product that has none. The site agrees it has none: every
    combo's own `unitLevelPrice` is empty."""
    combos = [r for r in rows if r.qty is None]

    assert [c["package_size"] for c in dataset].count("1 Combo") == 5
    assert len(combos) == 5
    assert all(r.unit is None and r.unit_price is None for r in combos)
    assert all(r.price is not None for r in combos)


def test_out_of_stock_rows_are_kept(rows) -> None:
    out = [r for r in rows if not r.in_stock]

    assert len(out) == 13
    # They carry a price, so the validation gate keeps them and the zero-rows
    # taxonomy can tell "out of stock" from "collector broke".
    kept, dropped, _ = validate_rows(out)
    assert len(kept) == 13 and dropped == 0


def test_instamart_reports_no_stock_count(rows) -> None:
    """`available_quantity` is null on every row: the payload has no stock
    count, only an in/out flag and a per-order cap. None is the honest answer;
    a zero would read as "out of stock"."""
    assert {r.qty_available for r in rows} == {None}


def test_page_level_provenance_rides_on_every_row(rows) -> None:
    assert all("560001" in (r.resolved_area or "") for r in rows)
    assert all(r.captured_at for r in rows)
    # The last row's own eta is null and inherits the dataset's.
    assert {r.eta_min for r in rows} == {13}


def test_the_pod_is_recorded_as_provenance(dataset) -> None:
    """Instamart's `podId` plays the part Blinkit's merchant id and Zepto's
    dark-store UUID play: it names the shelf that was read. It does not decide
    whether the rows are served."""
    assert {record["store_id"] for record in dataset} == {POD}
    assert store_ids_in(dataset) == [POD]
    assert known_store([POD], "560001", {"560001": {POD}}) is True
    assert known_store([POD], "560001", {"560001": {"930099"}}) is False
    assert known_store([POD], "560001", {}) is None


def test_instamarts_resolved_line_carries_the_pincode(rows) -> None:
    """The fixture's delivery line carries the requested pincode."""
    assert all(resolves_pincode(r, "560001") for r in rows)
    assert not any(resolves_pincode(r, "110001") for r in rows)


def test_the_serp_file_object_is_not_mistaken_for_a_download(dataset) -> None:
    """The Bright Data file object carries `url` — but it is the address of the
    page that was photographed, not a link to the PNG. Following it would have
    stored the live search page's HTML as `serp.png`. We take no URL, the
    universe reports `artifact_failed`, and the rows still stand."""
    shot = next(r["serp_screenshot"] for r in dataset if r["serp_screenshot"])

    assert shot["__type__"] == "file"
    assert shot["url"].startswith("https://")  # tempting, and wrong
    assert "screenshot_url" not in screenshot_record(dataset)


# -- raw search response ------------------------------------------------------
def test_one_row_per_variation_not_per_item(search_rows) -> None:
    """32 items, 72 variations. A variation is one buyable pack with its own
    skuId and price, so it is the row — collapsing to the item would lose every
    pack size but one."""
    assert len(search_rows) == 72
    assert {r.universe for r in search_rows} == {"instamart"}
    # The filter/sort widget sits in the same `cards[]` list and is not a
    # product: it has no gridElements, and it contributes nothing.
    assert all(r.raw_ref and r.raw_ref.startswith("cards[") for r in search_rows)
    assert not any(r.raw_ref.startswith("cards[0]") for r in search_rows)


def test_money_arrives_as_units_and_nanos(search_rows) -> None:
    """`{"units": "63", "nanos": 0}` — and `units` is a STRING on the wire."""
    body = load("instamart_search_response.json")[0]["search_response"]
    variation = body["data"]["cards"][1]["card"]["card"]["gridElements"]["infoWithStyle"][
        "items"
    ][0]["variations"][2]

    assert variation["price"]["offerPrice"]["units"] == "63"
    assert isinstance(variation["price"]["offerPrice"]["units"], str)

    butter = one(search_rows, "Amul Pasteurised Butter", 100.0)
    assert butter.price == 63.0 and butter.mrp == 63.0


def test_the_sites_own_unit_price_is_preferred(search_rows) -> None:
    """`unitLevelPrice` is on 49 of the 72 variations and agrees with our own
    arithmetic on every one of them, so preferring it changes no number here —
    it just stops us contradicting the site where we could."""
    four_pack = one(search_rows, "Amul Pasteurised Butter", 400.0)

    assert (four_pack.unit_price, four_pack.unit_price_basis) == (63.0, "per 100 g")

    for row in search_rows:
        if row.price and row.qty and row.unit == "g":
            assert row.unit_price == pytest.approx(row.price / row.qty * 100, abs=0.51)


def test_eta_comes_from_the_per_pod_delivery_sla(search_rows) -> None:
    """Item-level `sla` is null on every variation in the capture, so the ETA
    lives in the page config keyed by pod."""
    assert {r.eta_min for r in search_rows} == {6}


def test_sponsorship_is_an_item_level_fact(search_rows) -> None:
    """Four promoted items, three variations each. Both markers — the
    `BADGE_TYPE_AD` badge and `adTrackingContext` — agree on the same items."""
    sponsored = [r for r in search_rows if r.sponsored]

    assert len(sponsored) == 12
    assert {r.brand for r in sponsored} == {"Akshayakalpa"}
    # BADGE_TYPE_INSTA_UPGRADE also contains "AD" as a substring and must not
    # count: the item carrying it is not sponsored.
    assert any(r.name.startswith("NOICE") and not r.sponsored for r in search_rows)


def test_out_of_stock_variations_are_kept(search_rows) -> None:
    out = [r for r in search_rows if not r.in_stock]

    assert len(out) == 10
    assert all(r.price is not None for r in out)


def test_junk_payloads_yield_no_rows_rather_than_raising() -> None:
    assert map_instamart({}) == []
    assert map_instamart([]) == []
    assert map_instamart("not a payload") == []
    assert map_instamart({"data": {"cards": []}}) == []
    assert map_instamart({"data": {"cards": [{"card": {"card": {"@type": "x"}}}]}}) == []


def test_a_bare_site_body_is_accepted_without_the_wrapper() -> None:
    body = load("instamart_search_response.json")[0]["search_response"]

    assert len(map_instamart(body)) == 72
    assert len(map_instamart(body["data"])) == 72


# -- the two shapes must agree ------------------------------------------------
def test_both_shapes_read_a_pack_size_the_same_way(rows, search_rows) -> None:
    """Two captures of the same shelf, arriving in two different shapes. Where
    they list the same product at the same size they must agree about what it
    weighs, or the collector-row path and the raw path would quietly disagree
    about the unit price."""
    flat = {(r.name, r.qty, r.unit) for r in rows}
    raw = {(r.name, r.qty, r.unit) for r in search_rows}
    shared_names = {n for n, _, _ in flat} & {n for n, _, _ in raw}

    assert len(shared_names) > 10
    for name in shared_names:
        flat_sizes = {(q, u) for n, q, u in flat if n == name}
        raw_sizes = {(q, u) for n, q, u in raw if n == name}
        assert flat_sizes & raw_sizes, name


# -- per-universe collector version -------------------------------------------
# These live here because Instamart is the case that needs them: its template is
# still a draft while Zepto's and Blinkit's are published, and the whole demo
# must not be dropped back to `dev` to run it.
def test_the_global_version_applies_to_every_universe(monkeypatch) -> None:
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION", "prod")
    # Set to empty, not deleted: `load_dotenv` fills in any key that is ABSENT,
    # so deleting one would let the developer's real .env answer instead.
    for uid in ("ZEPTO", "BLINKIT", "INSTAMART", "CHAOS"):
        monkeypatch.setenv(f"SVERSE_COLLECTOR_VERSION_{uid}", "")

    settings = build_settings()

    assert settings.collector_versions == {}
    assert all(
        settings.collector_version_for(uid) == "prod"
        for uid in ("zepto", "blinkit", "instamart", "chaos")
    )


def test_one_universe_can_be_pinned_to_the_dev_template(monkeypatch) -> None:
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION", "prod")
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION_INSTAMART", "dev")

    settings = build_settings()

    assert settings.collector_version_for("instamart") == "dev"
    assert settings.collector_version_for("zepto") == "prod"
    assert settings.collector_version == "prod"  # the default is untouched


def test_an_override_equal_to_the_default_is_not_recorded(monkeypatch) -> None:
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION", "prod")
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION_INSTAMART", "PROD")

    assert build_settings().collector_versions == {}


def test_an_unrecognised_override_falls_back_instead_of_being_sent(monkeypatch) -> None:
    """`/dca/trigger` understands `dev` and `prod`. A typo must not travel to
    Bright Data and fail the whole universe there."""
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION", "prod")
    monkeypatch.setenv("SVERSE_COLLECTOR_VERSION_INSTAMART", "staging")

    settings = build_settings()

    assert settings.collector_versions == {}
    assert settings.collector_version_for("instamart") == "prod"


def test_the_registry_reports_the_effective_version_per_universe(tmp_path) -> None:
    settings = make_settings(
        tmp_path / "runs",
        collector_version="prod",
        collector_versions={"instamart": "dev"},
    )

    rows = {u.id: u.collector_version for u in universes(settings)}

    assert rows["instamart"] == "dev"
    assert rows["zepto"] == rows["blinkit"] == rows["chaos"] == "prod"


def test_the_api_reports_the_effective_version_per_universe(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from server.app import create_app

    settings = make_settings(
        tmp_path / "runs",
        collector_version="prod",
        collector_versions={"instamart": "dev"},
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/universes").json()

    assert body["collector_version"] == "prod"  # the default
    versions = {u["id"]: u["collector_version"] for u in body["universes"]}
    assert versions["instamart"] == "dev"
    assert versions["zepto"] == "prod"


async def test_the_override_is_what_reaches_the_trigger(tmp_path) -> None:
    """The point of the whole feature: the dev template is actually requested,
    and only for the universe that asked for it."""
    settings = make_settings(
        tmp_path / "runs",
        bd_mode="live",
        bd_api_key="dummy-test-key",
        collector_version="prod",
        collector_versions={"instamart": "dev"},
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"collection_id": "j_test"})

    rows = {u.id: u for u in universes(settings)}
    client = LiveClient(settings, transport=httpx.MockTransport(handler))
    for uid in ("instamart", "zepto"):
        await client.trigger("c_test", [{"keyword": "amul butter"}], rows[uid].collector_version)
    await client.aclose()

    assert seen[0].url.params.get("version") == "dev"
    # `prod` is the API's default and is sent by OMITTING the parameter.
    assert "version" not in seen[1].url.params


# -- wiring -------------------------------------------------------------------
def test_the_registered_instamart_mapper_is_the_real_one() -> None:
    assert get_mapper("instamart") is map_instamart


def test_instamart_is_dispatchable_in_live_mode_once_its_collector_id_is_set(tmp_path) -> None:
    settings = make_settings(
        tmp_path / "runs",
        bd_mode="live",
        collector_ids={"zepto": "", "blinkit": "", "instamart": "c_fixture_instamart", "chaos": ""},
    )

    rows = {u.id: u for u in universes(settings)}

    assert (rows["instamart"].wired, rows["instamart"].dispatchable) == (True, True)
    assert rows["instamart"].status == "wired"
    assert rows["instamart"].collector_id == ""  # never leaves the process


def test_the_instamart_store_map_is_read_from_its_own_env_var(monkeypatch) -> None:
    monkeypatch.setenv("SVERSE_INSTAMART_STORE_MAP", f"560001:{POD}|930002")
    monkeypatch.setenv("SVERSE_BLINKIT_STORE_MAP", "")

    settings = build_settings()

    assert settings.store_map_for("instamart") == {"560001": frozenset({POD, "930002"})}
    assert settings.store_map_for("blinkit") == {}


# -- three universes ----------------------------------------------------------
def test_three_independently_captured_universes_produce_one_matched_group() -> None:
    """Zepto, Blinkit and Instamart, captured separately, in three different
    payload shapes. Amul Unsalted 100 g is the one product all three shelves
    carry, and all three price it at ₹65."""
    rows = {
        "zepto": map_zepto(load("zepto_collector_result.json")),
        "blinkit": map_blinkit(load("blinkit_search_response.json")),
        "instamart": map_instamart(load("instamart_collector_rows.json")),
    }

    comparison = match(rows)
    matched = {g.key: g for g in comparison.groups}

    assert comparison.universe_count == 3
    group = matched["amul|100|g|unsalted#0"]
    assert group.confidence == "close"
    assert group.universes == ["blinkit", "instamart", "zepto"]
    assert {r.universe: r.price for r in group.rows} == {
        "zepto": 65.0,
        "blinkit": 65.0,
        "instamart": 65.0,
    }
    # Nothing was matched across pack sizes. Compared in BASE units, because
    # Zepto says "1 KILO" where Instamart says "1000 g" — the same shelf fact.
    for group in comparison.groups:
        assert len({to_base_qty(r.qty, r.unit) for r in group.rows}) == 1


def test_a_brand_that_sells_many_things_at_one_size_is_not_one_group() -> None:
    """The defect this test used to pin, now fixed.

    Brand + quantity + unit + variant alone put every 200 g Amul product in one
    row: butter, margarine, paneer, cheese slices, cheese cubes and cookies were
    presented as the same product across universes. Instamart's 75 rows are what
    made it obvious. The resolver now requires the product NAMES to agree as
    well, so that bucket splits into one subgroup per product — and since no two
    universes list the same 200 g Amul product here, every one of them is
    single-source and reported as unmatched rather than as a comparison.
    """
    rows = {
        "zepto": map_zepto(load("zepto_collector_result.json")),
        "blinkit": map_blinkit(load("blinkit_search_response.json")),
        "instamart": map_instamart(load("instamart_collector_rows.json")),
    }

    comparison = match(rows)
    buckets = [g for g in comparison.groups + comparison.unmatched if g.key.startswith("amul|200|g|-")]
    names_per_bucket = [{r.name for r in g.rows} for g in buckets]

    # Paneer and butter are in the same brand/size/variant bucket and must not
    # share a subgroup.
    paneer = next(n for n in names_per_bucket if "Amul Fresh Paneer" in n)
    butter = next(n for n in names_per_bucket if "Amul Pasteurised Butter" in n)
    assert paneer != butter
    assert all(len(names) == 1 for names in names_per_bucket)
    assert all(g.confidence == "unmatched" for g in buckets)


def test_a_product_with_no_parseable_pack_size_is_never_cross_universe_matched() -> None:
    """`1 Combo` rows carry no comparable pack size, so qty and unit are None.
    Two listings whose sizes are both unknown are not known to be the same size —
    that is the whole reason the field is null — so they are kept apart by
    construction and can only ever be reported as single-source."""
    comparison = match(
        {
            "instamart": map_instamart(load("instamart_collector_rows.json")),
            "zepto": map_zepto(load("zepto_collector_result.json")),
        }
    )
    sizeless = [
        g
        for g in comparison.groups + comparison.unmatched
        if g.qty is None or g.unit is None
    ]

    assert sizeless, "the Instamart fixture carries combo rows with no pack size"
    assert all(len(g.universes) == 1 for g in sizeless)
    assert all(g.confidence == "unmatched" for g in sizeless)


def test_instamart_is_absent_from_the_mock_demo_until_that_is_a_decision(tmp_path) -> None:
    """Same rule as Blinkit: `BD_MODE=mock` dispatches on the filename
    `<universe>_collector_result.json`, and there deliberately is none for
    Instamart, so the mock run's row counts are unchanged by this pass."""
    settings = make_settings(tmp_path / "runs")

    row = {u.id: u for u in universes(settings)}["instamart"]

    assert not (FIXTURES / "instamart_collector_result.json").exists()
    assert (row.dispatchable, row.status) == (False, "no fixture")
