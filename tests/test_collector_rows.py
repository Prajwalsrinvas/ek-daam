"""The live collector path: flattened one-record-per-product dataset rows.

Covers the three things that differ from the mock path — rupee prices, the
per-row SERP capture, and the trimmed trigger input — plus the site-resolved
location proof.

PENDING CONFIRMATION: `zepto_collector_rows.json` is built from the frozen
17-field contract, not from real dataset output. Key casing and the
`serp_screenshot` serialization get checked against the first real dev run; see
`tests/fixtures/README.md`.
"""

from __future__ import annotations

import dataclasses
import json
import shutil

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import FIXTURES, carry_cookies, wait_for_done
from server import mappers
from server.app import create_app
from server.bd_client import LiveClient
from server.mappers import extract_screenshot_url, has_screenshot_reference, screenshot_record
from server.mappers.zepto import is_collector_row, map_zepto
from server.resolve import NormalizedRow
from server.runs import (
    ARTIFACT_NOT_DELIVERABLE,
    known_store,
    location_proof_error,
    resolves_pincode,
    split_by_location,
    store_ids_in,
    validate_rows,
)

STORE_ID = "store-fixture-560001"
SERP_URL = "https://example.invalid/scrapeverse/serp-zepto.png"
RESOLVED = "Bengaluru - 560001, Karnataka"


@pytest.fixture
def dataset() -> list[dict]:
    return json.loads((FIXTURES / "zepto_collector_rows.json").read_text(encoding="utf-8"))


@pytest.fixture
def rows(dataset) -> list[NormalizedRow]:
    return map_zepto(dataset)


def by_name(rows: list[NormalizedRow], name: str) -> NormalizedRow:
    return next(r for r in rows if r.name == name)


# -- shape detection ----------------------------------------------------------
def test_collector_rows_are_detected_by_name_and_price(dataset) -> None:
    assert all(is_collector_row(record) for record in dataset)


def test_the_wrapper_shape_is_not_mistaken_for_a_collector_row() -> None:
    wrapper = json.loads(
        (FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8")
    )

    assert not any(is_collector_row(record) for record in wrapper)
    assert not is_collector_row({"layout": []})
    assert not is_collector_row({"product_name": "x"})  # price is required too


def test_both_shapes_still_map(dataset) -> None:
    """The fallback path is what mock mode and every offline capture use."""
    wrapper = json.loads(
        (FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8")
    )

    assert len(map_zepto(dataset)) == 7
    assert len(map_zepto(wrapper)) == 7


# -- prices are already rupees ------------------------------------------------
def test_prices_are_not_divided_by_one_hundred(rows: list[NormalizedRow]) -> None:
    salted = by_name(rows, "Amul Salted Butter")

    # selling_price 309.0 stays 309.0. Dividing would give ₹3.09.
    assert salted.price == 309.00
    assert salted.mrp == 310.00
    assert salted.unit_price == 61.80
    assert salted.unit_price_basis == "per 100 g"


def test_no_price_lands_below_the_validation_band(rows: list[NormalizedRow]) -> None:
    """A stray /100 would push every row under the ₹1 floor and silently empty
    the run, so assert the gate keeps them all."""
    kept, dropped, reasons = validate_rows(rows)

    assert len(kept) == 7 and dropped == 0 and reasons == {}
    assert min(r.price for r in rows) >= 60.00


def test_null_discounted_price_falls_back_to_selling_price(rows: list[NormalizedRow]) -> None:
    heritage = by_name(rows, "Heritage Table Butter")

    assert heritage.price == 60.00
    assert heritage.mrp == 62.00


def test_the_contract_carries_no_member_price_field(dataset) -> None:
    """Zepto Pass cannot leak on this path because the collector never emits it.
    Guard the contract itself, so a future field addition trips this."""
    for record in dataset:
        assert "zeptoPassPrice" not in record
        assert "zepto_pass_price" not in record
        assert "super_saver_selling_price" not in record


# -- pack size ----------------------------------------------------------------
def test_multipack_text_resolves_to_the_total_quantity(rows: list[NormalizedRow]) -> None:
    """`package_size` is the only size signal on this path — the numeric pack
    fields do not exist — so "50 x 20 g" has to mean 1000 g, not 20 g."""
    chiplet = by_name(rows, "Amul Butter Chiplet")
    mist = by_name(rows, "Milky Mist Butter Chiplet")

    assert (chiplet.qty, chiplet.unit) == (1000.0, "g")
    assert chiplet.unit_price == 63.50
    assert (mist.qty, mist.unit) == (100.0, "g")
    assert mist.unit_price == 88.00


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 pack (100 g)", (100.0, "g")),
        ("1 pack (500 g)", (500.0, "g")),
        ("100 g", (100.0, "g")),
        ("1 pack (10 x 10 g)", (100.0, "g")),
        ("1 pack (50 x 20 g)", (1000.0, "g")),
        ("100 g X 2", (200.0, "g")),
        ("2 x 500 ml", (1000.0, "ml")),
        ("1 kg", (1.0, "kg")),
        ("6 pcs", (6.0, "pc")),
        ("", (None, None)),
        ("family pack", (None, None)),
    ],
)
def test_package_size_parsing(text: str, expected: tuple) -> None:
    from server.mappers.collector_rows import parse_packsize

    assert parse_packsize(text) == expected


# -- stock, sponsorship, nullables --------------------------------------------
def test_out_of_stock_row_keeps_its_price(rows: list[NormalizedRow]) -> None:
    nandini = by_name(rows, "Nandini Salted Butter")

    assert nandini.in_stock is False
    assert nandini.qty_available == 0
    assert nandini.price == 70.00


def test_sponsored_flag_comes_from_is_sponsored(rows: list[NormalizedRow]) -> None:
    sponsored = [r for r in rows if r.sponsored]

    assert [r.name for r in sponsored] == ["Nutralite Doodhshakti Probiotic Butter"]


def test_every_nullable_field_survives_being_null(rows: list[NormalizedRow]) -> None:
    heritage = by_name(rows, "Heritage Table Butter")

    assert heritage.qty_available is None
    assert heritage.image_url is None
    # available_quantity null with out_of_stock false still means buyable.
    assert heritage.in_stock is True


def test_eta_falls_back_to_whatever_row_carries_it(rows: list[NormalizedRow]) -> None:
    """ETA is page-level on Zepto but rides on per-product rows. The collector
    stamps every row; if it ever stamps only some, the rest inherit rather than
    leaving the column patchy."""
    assert by_name(rows, "Amul Salted Butter").eta_min == 12
    # This row's own eta_minutes is null.
    assert by_name(rows, "Heritage Table Butter").eta_min == 12
    assert all(r.eta_min == 12 for r in rows)


def test_no_eta_anywhere_means_no_eta() -> None:
    """The fallback fills in from real data or not at all — it never invents."""
    rows = map_zepto(
        [
            {"product_name": "A", "selling_price": 10.0, "eta_minutes": None},
            {"product_name": "B", "selling_price": 20.0},
        ]
    )

    assert [r.eta_min for r in rows] == [None, None]


def test_captured_at_reaches_the_normalized_row(rows: list[NormalizedRow]) -> None:
    """18th field: stamped by the collector at parse time on every row. It is
    what the comparison receipt shows as capture time."""
    assert all(r.captured_at == "2026-08-22T09:12:33.412Z" for r in rows)


def test_captured_at_is_passed_through_not_reformatted() -> None:
    """Read as an opaque string so any ISO8601 spelling survives; only the UI
    parses it. Unlike ETA it is never back-filled from another row — a capture
    time that is not this row's own would be a fabricated provenance claim."""
    rows = map_zepto(
        [
            {"product_name": "A", "selling_price": 10.0, "captured_at": "2026-08-22T09:12:33+00:00"},
            {"product_name": "B", "selling_price": 20.0, "captured_at": None},
        ]
    )

    assert rows[0].captured_at == "2026-08-22T09:12:33+00:00"
    assert rows[1].captured_at is None


# -- resolved area ------------------------------------------------------------
def test_resolved_area_reaches_the_row(rows: list[NormalizedRow]) -> None:
    """What the SITE said it resolved our pincode to. Provenance, reported next
    to our configured area label rather than in place of it."""
    assert all(
        r.resolved_area == "Bengaluru - 560001, Karnataka"
        for r in rows[:-1]
    )


def test_resolved_area_never_falls_back_from_another_row(dataset) -> None:
    """It used to. That was wrong, and it undid the fail-closed location proof.

    `resolved_area` is THIS row's evidence of where its price came from. Copying
    a neighbour's let one located row vouch for rows the site never located —
    which is exactly the failure the proof exists to catch, since a collector
    that quietly ignored the pincode looks like a page of rows with no resolved
    area. The fixture's last row has it null and it stays null.
    """
    assert dataset[-1]["resolved_area"] is None

    assert map_zepto(dataset)[-1].resolved_area is None


def test_no_resolved_area_anywhere_stays_none() -> None:
    rows = map_zepto(
        [
            {"product_name": "A", "selling_price": 10.0, "resolved_area": None},
            {"product_name": "B", "selling_price": 20.0},
        ]
    )

    assert [r.resolved_area for r in rows] == [None, None]


def test_resolved_area_also_flows_on_the_raw_response_branch() -> None:
    """It sits on the wrapper record there, so it applies to every row."""
    wrapper = json.loads((FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8"))

    rows = map_zepto(wrapper)

    assert all(r.resolved_area == wrapper[0]["resolved_area"] for r in rows)
    # A bare site payload carries no wrapper, so there is nothing to report.
    assert all(r.resolved_area is None for r in map_zepto(wrapper[0]["search_response"]))


def test_resolved_area_is_not_part_of_the_grouping_key() -> None:
    """Provenance must never become identity: two rows differing only in the area
    the site resolved still group together."""
    from server.resolve import group_key

    base = dict(universe="zepto", name="Amul Salted Butter", brand="Amul", qty=500.0, unit="g")
    here = NormalizedRow(**base, resolved_area="Bengaluru - 560001")
    there = NormalizedRow(**base, resolved_area="Somewhere Else - 560001")

    assert group_key(here) == group_key(there)


def test_variant_and_identity_fields(rows: list[NormalizedRow]) -> None:
    unsalted = by_name(rows, "Amul Unsalted Cooking Butter")

    assert unsalted.variant == "unsalted"
    assert unsalted.brand == "Amul"
    assert unsalted.product_id == "v-fixture-001"
    assert unsalted.raw_ref == "dataset[0]"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Amul Unsalted Butter", "unsalted"),
        ("Akshayakalpa Organic Cooking Butter Un-salted", "unsalted"),
        ("Akshayakalpa Organic Cooking Butter Un salted", "unsalted"),
        ("Amul Salted Butter", "salted"),
        ("Amul Table Butter", None),
    ],
)
def test_the_unsalted_spelling_a_site_uses_does_not_flip_the_variant(
    name: str, expected: str | None
) -> None:
    """A substring test for "salted" matches "Un-salted" too, so Instamart's
    spelling put unsalted butter in the SALTED bucket of the comparison — a
    wrong match presented as a match. The negative is checked first and tolerates
    the separator."""
    from server.mappers.collector_rows import variant_label

    assert variant_label(name) == expected


def test_mapping_is_pure(dataset) -> None:
    assert [r.model_dump() for r in map_zepto(dataset)] == [
        r.model_dump() for r in map_zepto(dataset)
    ]


def test_junk_rows_are_skipped_not_fatal() -> None:
    rows = map_zepto(
        [
            {"product_name": "Real Butter", "selling_price": 50.0, "package_size": "100 g"},
            {"product_name": "   ", "selling_price": 10.0},
            {"not": "a product row"},
        ]
    )

    assert [r.name for r in rows] == ["Real Butter"]


def test_booleans_are_not_read_as_numbers() -> None:
    """`True` is an `int` in Python; a bool in a numeric field must read as
    missing rather than as 1."""
    rows = map_zepto(
        [{"product_name": "Odd Butter", "selling_price": True, "available_quantity": True}]
    )

    assert rows[0].price is None
    assert rows[0].qty_available is None


# -- SERP screenshot ----------------------------------------------------------
def test_screenshot_is_found_on_whatever_row_carries_it(dataset) -> None:
    record = screenshot_record(dataset)

    assert dataset[0]["serp_screenshot"] is None  # not the first row
    assert record["screenshot_url"] == SERP_URL
    # The rest of the base record is preserved for downstream context.
    assert record["product_name"] == "Amul Unsalted Cooking Butter"


@pytest.mark.parametrize(
    "value",
    [
        SERP_URL,
        {"url": SERP_URL},
        {"file": SERP_URL},
        {"href": SERP_URL},
        {"name": "shot.png", "public_url": SERP_URL},
        [{"url": SERP_URL}],
        {"meta": {"nested": {"url": SERP_URL}}},
    ],
)
def test_screenshot_url_extraction_is_shape_tolerant(value) -> None:
    assert extract_screenshot_url(value) == SERP_URL


@pytest.mark.parametrize(
    "value", [None, "", "not-a-url", "/relative/path.png", {}, [], {"url": None}, 42]
)
def test_unrecognised_screenshot_shapes_yield_nothing_rather_than_raising(value) -> None:
    assert extract_screenshot_url(value) is None


def test_wrapper_screenshot_convention_still_works() -> None:
    wrapper = json.loads(
        (FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8")
    )

    assert screenshot_record(wrapper)["screenshot_url"].startswith("https://")


def test_a_dataset_with_no_capture_leaves_the_record_alone(dataset) -> None:
    stripped = [{**record, "serp_screenshot": None} for record in dataset]

    record = screenshot_record(stripped)

    assert "screenshot_url" not in record


# -- trigger input ------------------------------------------------------------
async def test_live_trigger_strips_the_universe_routing_hint(settings) -> None:
    """The collector input schema is strictly keyword/pincode with
    strict_input_normalize; an extra key 422s the whole batch."""
    live = dataclasses.replace(settings, bd_mode="live", bd_api_key="dummy-test-key")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"collection_id": "j_test"})

    client = LiveClient(live, transport=httpx.MockTransport(handler))
    await client.trigger(
        "c_test",
        [{"universe": "zepto", "keyword": "amul butter", "pincode": "560001"}],
        "dev",
    )
    await client.aclose()

    assert json.loads(seen[0].content) == [
        {"keyword": "amul butter", "pincode": "560001"}
    ]


def test_a_run_sends_the_collector_a_keyword_and_a_pincode_and_nothing_else(
    rows_mode_settings, monkeypatch
) -> None:
    """No coordinates anywhere. The collector types the pincode into the site's
    own location picker, so lat/long were only ever dead weight — and the one
    pair the app carried was a real home address."""
    from server.bd_client import MockClient

    seen: list[dict] = []
    original = MockClient.trigger

    async def record(self, collector_id, inputs, version):
        seen.extend(inputs)
        return await original(self, collector_id, inputs, version)

    monkeypatch.setattr(MockClient, "trigger", record)

    with TestClient(create_app(rows_mode_settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

    assert seen == [{"universe": "zepto", "keyword": "amul butter", "pincode": "560001"}]


async def test_mock_trigger_still_uses_universe_to_pick_a_fixture(settings) -> None:
    from server.bd_client import MockClient

    client = MockClient(settings)

    job_id = await client.trigger("", [{"universe": "zepto", "keyword": "amul butter"}], "dev")

    assert "zepto" in job_id


# -- location proof: what the SITE resolved -----------------------------------
def test_a_row_the_site_resolved_to_our_pincode_passes(rows) -> None:
    """The whole test: the site's own delivery-address line names the pincode we
    asked for. The fixture's last row carries no resolved area at all, so it
    proves nothing and does not pass — which is the point."""
    located = rows[:-1]

    assert all("560001" in (r.resolved_area or "") for r in located)
    assert all(resolves_pincode(r, "560001") for r in located)
    assert resolves_pincode(rows[-1], "560001") is False


def test_a_row_the_site_resolved_somewhere_else_does_not_pass(rows) -> None:
    elsewhere = rows[0].model_copy(update={"resolved_area": "Connaught Place - 110001, Delhi"})

    assert resolves_pincode(elsewhere, "560001") is False


@pytest.mark.parametrize(
    "resolved",
    [
        "Bengaluru - 560001, Karnataka",  # zepto
        "Bengaluru, Karnataka 560001, India",                    # blinkit
        "Delivery to 560001, bengaluru, karnataka",  # instamart
    ],
)
def test_all_three_sites_spell_the_proof_differently_and_all_three_pass(resolved) -> None:
    """Representative site formats all pass on the same pincode boundary."""
    row = NormalizedRow(universe="zepto", name="Butter", price=50.0, resolved_area=resolved)

    assert resolves_pincode(row, "560001") is True


@pytest.mark.parametrize("resolved", [None, "", "Bengaluru, Karnataka, India"])
def test_a_row_with_no_resolved_pincode_proves_nothing_and_is_dropped(resolved) -> None:
    """Fail-CLOSED, and deliberately the opposite of the old store-id rule. The
    resolved line IS the proof now, so its absence is not a missing detail — it
    is the exact shape of a collector that ignored the pincode."""
    row = NormalizedRow(universe="zepto", name="Butter", price=50.0, resolved_area=resolved)

    assert resolves_pincode(row, "560001") is False


def test_an_empty_pincode_can_never_be_proved() -> None:
    row = NormalizedRow(universe="zepto", name="Butter", price=50.0, resolved_area="anywhere")

    assert resolves_pincode(row, "") is False


@pytest.mark.parametrize(
    "resolved",
    [
        "Delivery to 1560001, somewhere",       # a longer number that contains ours
        "Delivery to 5600011, somewhere",       # ...on the other side
        "call the store on 9995600018",         # a phone number that embeds it
        "order id 15600010",                    # any digit string, really
    ],
)
def test_a_pincode_buried_inside_a_longer_number_is_not_a_proof(resolved: str) -> None:
    """Plain containment passed all of these. None of them is the site saying it
    serves 560001, and treating one as proof publishes another area's shelf under
    this pincode."""
    row = NormalizedRow(universe="zepto", name="Butter", price=50.0, resolved_area=resolved)

    assert resolves_pincode(row, "560001") is False


@pytest.mark.parametrize(
    "resolved",
    [
        "Bengaluru - 560001, Karnataka",
        "Bengaluru, Karnataka 560001, India",
        "560001",
        "pincode:560001",
        "Delivery to 560001, bengaluru, karnataka",
    ],
)
def test_the_pincode_on_its_own_digit_boundary_still_proves(resolved: str) -> None:
    """A pincode next to punctuation, spaces, or the line end still passes."""
    row = NormalizedRow(universe="zepto", name="Butter", price=50.0, resolved_area=resolved)

    assert resolves_pincode(row, "560001") is True


def test_split_keeps_the_resolved_rows_and_hands_back_the_rest(rows) -> None:
    mixed = list(rows) + [
        rows[0].model_copy(update={"resolved_area": "Connaught Place - 110001, Delhi"}),
        rows[0].model_copy(update={"resolved_area": None}),
    ]

    located, unlocated = split_by_location(mixed, "560001")

    # The fixture's own last row carries no resolved area either, so three rows
    # in this list prove nothing about location.
    assert len(located) == len(rows) - 1
    assert len(unlocated) == 3
    assert [r.resolved_area for r in unlocated] == [
        None,
        "Connaught Place - 110001, Delhi",
        None,
    ]


# -- store ids: provenance, never a refusal -----------------------------------
def test_store_ids_are_collected_distinct_and_sorted(dataset) -> None:
    mixed = [dict(record) for record in dataset]
    mixed[3]["store_id"] = "store-fixture-longtail"

    assert store_ids_in(mixed) == sorted([STORE_ID, "store-fixture-longtail"])


@pytest.mark.parametrize(
    "records",
    [
        [],
        ["junk"],
        [{"product_name": "x"}],
        [{"store_id": None}],
        [{"store_id": "  "}],
        [{"store_id": True}],  # a bool is an int in Python and is not an id
    ],
)
def test_a_payload_with_no_usable_store_id_reports_none(records) -> None:
    assert store_ids_in(records) == []


def test_a_numeric_store_id_is_recorded_as_text() -> None:
    """Blinkit and Instamart report numbers; the event should not care which
    JSON type a site chose."""
    assert store_ids_in([{"store_id": 34540}]) == ["34540"]


def test_known_store_is_true_when_every_id_is_one_we_have_seen() -> None:
    assert known_store([STORE_ID], "560001", {"560001": {STORE_ID}}) is True


def test_known_store_is_false_when_an_id_is_new() -> None:
    """False is a note, not a refusal: the site already told us it resolved our
    pincode, so an unfamiliar store is news about the map, not about the rows."""
    assert known_store([STORE_ID, "brand-new-store"], "560001", {"560001": {STORE_ID}}) is False


@pytest.mark.parametrize(
    ("store_ids", "store_map"),
    [
        ([STORE_ID], {}),                       # no map configured for this universe
        ([STORE_ID], {"560001": frozenset()}),  # empty allowed set
        ([STORE_ID], {"110001": {STORE_ID}}),   # map has no entry for this pincode
        ([], {"560001": {STORE_ID}}),           # payload reported no store at all
    ],
)
def test_known_store_is_unknown_when_there_is_nothing_to_compare(store_ids, store_map) -> None:
    assert known_store(store_ids, "560001", store_map) is None


def test_store_map_parses_from_env_format() -> None:
    """The single-id spelling is unchanged; `|` adds a second allowed store."""
    from server.config import _parse_store_map

    assert _parse_store_map("560001:abc;110001:def") == {
        "560001": frozenset({"abc"}),
        "110001": frozenset({"def"}),
    }
    assert _parse_store_map(" 560001 : abc ") == {"560001": frozenset({"abc"})}
    assert _parse_store_map("560001:34540|12345") == {"560001": frozenset({"34540", "12345"})}
    assert _parse_store_map("560001: 34540 | 12345 |") == {
        "560001": frozenset({"34540", "12345"})
    }
    assert _parse_store_map("") == {}
    assert _parse_store_map("garbage;560001:") == {}


def test_store_maps_are_read_per_universe(monkeypatch) -> None:
    """A Blinkit merchant id must never be checked against the Zepto UUID."""
    from server.config import _load_store_maps

    monkeypatch.setenv("SVERSE_ZEPTO_STORE_MAP", "560001:zepto-store")
    monkeypatch.setenv("SVERSE_BLINKIT_STORE_MAP", "560001:34540|12345")
    monkeypatch.delenv("SVERSE_INSTAMART_STORE_MAP", raising=False)
    monkeypatch.delenv("SVERSE_CHAOS_STORE_MAP", raising=False)

    maps = _load_store_maps()

    assert maps == {
        "zepto": {"560001": frozenset({"zepto-store"})},
        "blinkit": {"560001": frozenset({"34540", "12345"})},
    }
    # A universe with no map configured is not checked at all.
    assert maps.get("instamart") is None


def zepto_rows_settings(settings, tmp_path, records: list[dict], name: str = "row-fixtures"):
    """Settings whose mock driver serves `records` as the Zepto dataset.

    Lets a test say what the SITE resolved without touching the committed
    fixture, which is what the new location proof reads.
    """
    fixtures = tmp_path / name
    fixtures.mkdir()
    (fixtures / "zepto_collector_result.json").write_text(
        json.dumps(records), encoding="utf-8"
    )
    return dataclasses.replace(settings, fixtures_dir=fixtures)


@pytest.fixture
def rows_mode_settings(settings, tmp_path):
    """Serve the live-shaped dataset through the mock driver, so the whole
    orchestration path — poll, raw save, location proof, map, gate — runs against
    real collector rows rather than the wrapper fixture."""
    fixtures = tmp_path / "row-fixtures"
    fixtures.mkdir()
    shutil.copy(FIXTURES / "zepto_collector_rows.json", fixtures / "zepto_collector_result.json")
    return dataclasses.replace(settings, fixtures_dir=fixtures)


def test_collector_rows_run_end_to_end_through_the_orchestration(rows_mode_settings) -> None:
    guarded = dataclasses.replace(rows_mode_settings, store_maps={"zepto": {"560001": {STORE_ID}}})

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    validated = next(e for e in snapshot["events"] if e["type"] == "validated")
    groups = snapshot["comparison"]["groups"] + snapshot["comparison"]["unmatched"]
    prices = {r["name"]: r["price"] for g in groups for r in g["rows"]}

    # The fixture's last row carries no resolved area, so it is dropped by the
    # location proof rather than borrowing a neighbour's.
    assert validated["data"] == {
        "rows_kept": 6,
        "rows_dropped": 1,
        "reasons": {"unresolved_location": 1},
        "store_ids": [STORE_ID],
        "known_store": True,
    }
    assert prices["Amul Salted Butter"] == 309.00  # rupees, not 3.09
    assert prices["Amul Butter Chiplet"] == 635.00


def test_an_unknown_store_is_recorded_and_still_served(rows_mode_settings) -> None:
    """The advisory map is not a gate. The site resolved our pincode, so the
    rows stand; `known_store: false` is how the run says the id is new to us."""
    advised = dataclasses.replace(
        rows_mode_settings, store_maps={"zepto": {"560001": {"a-store-we-have-not-seen"}}}
    )

    with TestClient(create_app(advised)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    validated = next(e for e in snapshot["events"] if e["type"] == "validated")

    assert validated["data"]["known_store"] is False
    assert validated["data"]["store_ids"] == [STORE_ID]
    assert validated["data"]["rows_kept"] == 6          # served anyway
    assert snapshot["comparison"]["row_count"] == 6


def test_no_store_map_reports_the_ids_without_judging_them(rows_mode_settings) -> None:
    with TestClient(create_app(rows_mode_settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    validated = next(e for e in snapshot["events"] if e["type"] == "validated")

    assert validated["data"]["store_ids"] == [STORE_ID]
    assert validated["data"]["known_store"] is None


def test_a_universe_the_site_never_resolved_fails_and_serves_nothing(
    settings, tmp_path, dataset
) -> None:
    """Refusing wrong-location data beats serving it: no screenshot, no
    comparison — just an honest failure naming the pincode."""
    elsewhere = [
        {**record, "resolved_area": "Connaught Place - 110001, New Delhi"} for record in dataset
    ]
    guarded = zepto_rows_settings(settings, tmp_path, elsewhere)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]
    failed = next(e for e in snapshot["events"] if e["type"] == "failed")

    assert failed["data"]["error"] == location_proof_error("560001")
    assert failed["data"]["error"] == "location proof failed: site did not resolve 560001"
    assert failed["data"]["pincode"] == "560001"
    # The store ids are still recorded — that is what a refusal is worth debugging with.
    assert failed["data"]["store_ids"] == [STORE_ID]
    assert "screenshot" not in types and "validated" not in types
    assert "rows" in types  # the rows were really parsed; they were then refused
    assert types[-1] == "done"  # the run still settles honestly
    assert snapshot["comparison"]["row_count"] == 0


def test_a_collector_that_returned_nothing_reports_zero_rows_not_a_location_failure(
    settings, tmp_path
) -> None:
    """An empty payload is not evidence about location.

    Zero mapped rows used to emit `failed: location proof failed: site did not
    resolve <pincode>` — a reason the run never observed. There is nothing to
    prove or disprove about where prices came from when there are no prices, so
    the zero-rows taxonomy handles it and its honest default is `broken`.
    """
    guarded = zepto_rows_settings(settings, tmp_path, [])

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]
    zero = next(e for e in snapshot["events"] if e["type"] == "zero_rows")

    assert "failed" not in types
    assert zero["data"]["reason"] == "broken"
    assert zero["data"]["rows_seen"] == 0
    assert "validated" not in types and "screenshot" not in types
    assert types[-1] == "done"
    assert snapshot["comparison"]["row_count"] == 0


def test_an_empty_payload_that_says_why_is_reported_with_that_reason(
    settings, tmp_path
) -> None:
    """When the collector DID say something — unserviceable, blocked — the
    taxonomy reports it rather than falling back to `broken`."""
    guarded = zepto_rows_settings(
        settings, tmp_path, [{"message": "This location is unserviceable"}]
    )

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    zero = next(e for e in snapshot["events"] if e["type"] == "zero_rows")

    assert zero["data"]["reason"] == "unserviceable"
    assert "failed" not in [e["type"] for e in snapshot["events"]]


def test_a_payload_with_no_resolved_area_at_all_is_refused(settings, tmp_path, dataset) -> None:
    """The collector that quietly ignores the pincode looks exactly like this.
    Serving its rows would publish some default store's shelf as this area's."""
    blank = [{**record, "resolved_area": None} for record in dataset]
    guarded = zepto_rows_settings(settings, tmp_path, blank)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    failed = next(e for e in snapshot["events"] if e["type"] == "failed")

    assert failed["data"]["error"] == location_proof_error("560001")
    assert snapshot["comparison"]["row_count"] == 0


def test_rows_the_site_did_not_resolve_are_dropped_and_named(settings, tmp_path, dataset) -> None:
    """Mixed page: some rows carry our pincode, some carry another area's. The
    ones that do not are dropped with a named reason rather than refusing the
    universe or being served alongside the good ones."""
    mixed = [dict(record) for record in dataset]
    for record in mixed[5:]:
        record["resolved_area"] = "Connaught Place - 110001, New Delhi"
    guarded = zepto_rows_settings(settings, tmp_path, mixed)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    validated = next(e for e in snapshot["events"] if e["type"] == "validated")

    assert validated["data"]["rows_kept"] == 5
    assert validated["data"]["rows_dropped"] == 2
    assert validated["data"]["reasons"] == {"unresolved_location": 2}
    # kept + dropped still accounts for every row the mapper parsed
    assert next(e for e in snapshot["events"] if e["type"] == "rows")["data"]["n"] == 7
    assert snapshot["comparison"]["row_count"] == 5


def test_the_store_ids_are_recorded_in_the_feed_as_provenance(rows_mode_settings) -> None:
    """The old build kept store ids out of the feed. They are not secrets — any
    logged-out browser sees them — and a run that will not say which shelf it
    read is harder to check than one that does."""
    with TestClient(create_app(rows_mode_settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)
        events_text = json.dumps(client.get(f"/api/runs/{run_id}").json()["events"])
        raw_text = (rows_mode_settings.runs_dir / run_id / "raw" / "zepto.json").read_text(
            encoding="utf-8"
        )

    assert STORE_ID in events_text
    assert STORE_ID in raw_text


def test_refused_rows_do_not_come_back_after_a_restart(settings, tmp_path, dataset) -> None:
    """The raw payload is written before the location check, so it is on disk
    even for a refused universe. A fresh process must not re-derive rows from it
    — the refusal has to outlive the process that made it."""
    elsewhere = [
        {**record, "resolved_area": "Connaught Place - 110001, New Delhi"} for record in dataset
    ]
    guarded = zepto_rows_settings(settings, tmp_path, elsewhere)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

    assert (guarded.runs_dir / run_id / "raw" / "zepto.json").is_file()

    # Fresh app: nothing about this run is in memory any more. Same visitor
    # though, so the run is still theirs to read.
    with TestClient(create_app(guarded)) as restarted:
        client = carry_cookies(client, restarted)
        comparison = client.get(f"/api/runs/{run_id}").json()["comparison"]

    assert comparison["row_count"] == 0
    assert comparison["groups"] == [] and comparison["unmatched"] == []


def test_rows_dropped_for_location_stay_dropped_after_a_restart(
    settings, tmp_path, dataset
) -> None:
    """Same rule at row level: re-deriving from raw must apply the same location
    proof the live run applied, or a restart would quietly re-admit the rows the
    run refused."""
    mixed = [dict(record) for record in dataset]
    for record in mixed[5:]:
        record["resolved_area"] = "Connaught Place - 110001, New Delhi"
    guarded = zepto_rows_settings(settings, tmp_path, mixed)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        wait_for_done(client, run_id)

    # Same visitor, fresh process: the run is still theirs to read.
    with TestClient(create_app(guarded)) as restarted:
        client = carry_cookies(client, restarted)
        assert client.get(f"/api/runs/{run_id}").json()["comparison"]["row_count"] == 5


def test_zero_rows_universes_do_not_come_back_after_a_restart(settings, monkeypatch) -> None:
    """Same rule, different refusal: a universe that reported `zero_rows` wrote
    its raw payload too, and must not be resurrected from it either."""
    monkeypatch.setitem(
        mappers.MAPPERS,
        "zepto",
        lambda payload: [
            NormalizedRow(
                universe="zepto",
                name="Amul Salted Butter",
                price=309.0,
                in_stock=False,
                # The site resolved us fine; the shelf is simply empty. Without
                # this the run would refuse on location instead, which is a
                # different — and here wrong — answer.
                resolved_area="Bengaluru - 560001, Karnataka",
            )
        ],
    )

    with TestClient(create_app(settings)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    assert next(e for e in snapshot["events"] if e["type"] == "zero_rows")["data"]["reason"] == "oos"
    assert snapshot["comparison"]["row_count"] == 0

    # Same visitor, fresh process: the run is still theirs to read.
    with TestClient(create_app(settings)) as restarted:
        client = carry_cookies(client, restarted)
        assert client.get(f"/api/runs/{run_id}").json()["comparison"]["row_count"] == 0


def test_an_undeliverable_capture_is_not_reported_as_a_failure(
    settings, tmp_path, dataset, monkeypatch
) -> None:
    """The undeliverable SERP capture is ignored, not announced.

    The dataset carries a SERP reference on row 3 and the client cannot download
    it, which is true of EVERY live universe on every run: Bright Data does not
    serve collector media over the API. Reporting it put an amber "no page
    capture" line under every universe that had actually succeeded, so the run
    now says nothing about a capture it was never going to have.
    """

    async def undeliverable(self, record, universe_id):
        return None

    monkeypatch.setattr("server.bd_client.MockClient.fetch_screenshot", undeliverable)
    guarded = zepto_rows_settings(settings, tmp_path, dataset)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]

    assert "artifact_failed" not in types
    assert "screenshot" not in types  # nothing to show, and nothing invented
    assert "validated" in types  # the rows still stand
    assert types[-1] == "done"
    # The explanation still exists in one place, it is just not an event.
    assert ARTIFACT_NOT_DELIVERABLE == (
        "capture exists in Bright Data but is not deliverable via API"
    )


def test_a_payload_with_no_capture_at_all_says_nothing_about_one(
    settings, tmp_path, dataset, monkeypatch
) -> None:
    """A collector that reported no capture at all is a different fact from one
    whose capture cannot be delivered, and neither is announced. The run says
    nothing about a screenshot in either case, and never invents one."""

    async def undeliverable(self, record, universe_id):
        return None

    monkeypatch.setattr("server.bd_client.MockClient.fetch_screenshot", undeliverable)
    stripped = [
        {k: v for k, v in record.items() if k not in ("serp_screenshot", "screenshot_url")}
        for record in dataset
    ]
    guarded = zepto_rows_settings(settings, tmp_path, stripped)

    with TestClient(create_app(guarded)) as client:
        run_id = client.post(
            "/api/runs", json={"query": "amul butter", "pincode": "560001"}
        ).json()["run_id"]
        snapshot = wait_for_done(client, run_id)

    types = [e["type"] for e in snapshot["events"]]

    assert "artifact_failed" not in types
    assert "screenshot" not in types
    assert "validated" in types


async def test_the_serp_capture_is_found_but_never_downloaded(settings, dataset) -> None:
    """Close the loop on the one true sentence: Bright Data captures a SERP
    screenshot per run, it is not deliverable via API, so the app shows none.

    `screenshot_record` still digs the reference out of row 3, which is how the
    app knows a capture exists at all, and the live client makes no request for
    it. The app shows nothing and says nothing about it.
    """
    live = dataclasses.replace(settings, bd_mode="live", bd_api_key="dummy-test-key")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nshot")

    client = LiveClient(live, transport=httpx.MockTransport(handler))
    blob = await client.fetch_screenshot(screenshot_record(dataset), "zepto")
    await client.aclose()

    assert has_screenshot_reference(dataset) is True
    assert blob is None
    assert seen == []


# -- one product, one row -----------------------------------------------------
def test_the_same_product_delivered_twice_becomes_one_row() -> None:
    """Blinkit's collector merges two result pages, so a product that appears on
    both arrives twice. A duplicate is not a second shelf listing: counted twice
    it inflates `rows_kept`, shows the same price twice in one comparison cell,
    and makes a universe look like it stocks more than it does."""
    twice = [
        {
            "product_name": "Amul Salted Butter",
            "product_id": "p-1",
            "selling_price": 63.0,
            "package_size": "100 g",
            "resolved_area": RESOLVED,
        },
        {
            "product_name": "Amul Salted Butter",
            "product_id": "p-1",
            "selling_price": 63.0,
            "package_size": "100 g",
            "resolved_area": RESOLVED,
        },
        {
            "product_name": "Amul Unsalted Butter",
            "product_id": "p-2",
            "selling_price": 65.0,
            "package_size": "100 g",
            "resolved_area": RESOLVED,
        },
    ]

    rows = map_zepto(twice)

    assert [r.product_id for r in rows] == ["p-1", "p-2"]


def test_rows_without_a_product_id_are_never_deduped_away() -> None:
    """A missing id is not evidence that two listings are the same listing. Two
    distinct products with no id must both survive."""
    anonymous = [
        {"product_name": "Butter A", "selling_price": 60.0, "resolved_area": RESOLVED},
        {"product_name": "Butter B", "selling_price": 70.0, "resolved_area": RESOLVED},
    ]

    rows = map_zepto(anonymous)

    assert [r.name for r in rows] == ["Butter A", "Butter B"]


# -- store ids: JSON has one number type --------------------------------------
def test_an_integer_valued_float_store_id_is_reported_without_its_decimal() -> None:
    """A numeric merchant id can arrive as 34540.0. `str()` on that is "34540.0",
    which matches nothing in a store map — so `known_store` read false for a
    store the operator had listed correctly."""
    assert store_ids_in([{"store_id": 34540.0}]) == ["34540"]
    assert store_ids_in([{"store_id": 34540}]) == ["34540"]
    assert store_ids_in([{"store_id": "34540"}]) == ["34540"]
    # Only integer-valued floats are rewritten; a genuinely fractional id (which
    # no site issues) is not silently truncated.
    assert store_ids_in([{"store_id": 3.5}]) == ["3.5"]


def test_a_float_store_id_matches_the_configured_map() -> None:
    ids = store_ids_in([{"store_id": 34540.0}])

    assert known_store(ids, "560001", {"560001": frozenset({"34540"})}) is True
