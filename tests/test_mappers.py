"""Zepto mapper — the paise conversion, the member-price exclusion, stock, and
the sponsored slot. DESIGN.md §6.
"""

from __future__ import annotations

import pytest

from server.mappers import MAPPERS, get_mapper, mapper_is_stub
from server.resolve import NormalizedRow, match, unit_price

MEMBER_PRICE_FIELDS = ("zeptoPassPrice", "superSaverSellingPrice")


@pytest.fixture
def rows(zepto_fixture) -> list[NormalizedRow]:
    return get_mapper("zepto")(zepto_fixture)


def by_name(rows: list[NormalizedRow], name: str) -> NormalizedRow:
    return next(r for r in rows if r.name == name)


def test_every_product_in_the_payload_becomes_a_row(rows: list[NormalizedRow]) -> None:
    # 6 grid products + 1 sponsored product nested in the ads widget.
    assert len(rows) == 7
    assert {r.universe for r in rows} == {"zepto"}


def test_prices_are_paise_divided_by_one_hundred(rows: list[NormalizedRow]) -> None:
    salted = by_name(rows, "Amul Salted Butter")

    # sellingPrice 30900 paise, mrp 31000 paise
    assert salted.price == 309.00
    assert salted.mrp == 310.00


def test_member_prices_are_never_reported(zepto_fixture, rows: list[NormalizedRow]) -> None:
    """The hard product rule: no Zepto Pass / super-saver pricing, anywhere.

    Every fixture row carries member prices strictly below `sellingPrice`, so if
    the mapper ever reached for one this would catch it.
    """
    member_paise: set[int] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for field in MEMBER_PRICE_FIELDS:
                value = node.get(field)
                if isinstance(value, (int, float)) and value > 0:
                    member_paise.add(int(value))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(zepto_fixture)
    assert len(member_paise) >= 10, "fixture must actually carry member prices"

    reported = {round(r.price * 100) for r in rows if r.price is not None}
    assert reported.isdisjoint(member_paise)


def test_out_of_stock_row_is_kept_and_flagged(rows: list[NormalizedRow]) -> None:
    nandini = by_name(rows, "Nandini Salted Butter")

    assert nandini.in_stock is False
    assert nandini.qty_available == 0
    assert nandini.price == 70.00  # the shelf price is still reported


def test_kilo_pack_normalises_to_grams(rows: list[NormalizedRow]) -> None:
    chiplet = by_name(rows, "Amul Butter Chiplet")

    assert (chiplet.qty, chiplet.unit) == (1.0, "kg")
    assert chiplet.unit_price == 63.50
    assert chiplet.unit_price_basis == "per 100 g"


def test_unit_price_is_per_hundred_grams(rows: list[NormalizedRow]) -> None:
    salted = by_name(rows, "Amul Salted Butter")

    assert salted.unit_price == 61.80  # 309 / 500 g * 100
    assert salted.unit_price_basis == "per 100 g"


def test_sponsored_slot_is_flagged_not_dropped(rows: list[NormalizedRow]) -> None:
    sponsored = [r for r in rows if r.sponsored]

    assert len(sponsored) == 1
    assert sponsored[0].name == "Nutralite Doodhshakti Probiotic Butter"
    assert sponsored[0].price == 279.00


def test_variant_is_read_from_the_name(rows: list[NormalizedRow]) -> None:
    assert by_name(rows, "Amul Unsalted Cooking Butter").variant == "unsalted"
    assert by_name(rows, "Amul Salted Butter").variant == "salted"
    assert by_name(rows, "Milky Mist Butter Chiplet").variant is None


def test_zero_price_row_maps_to_no_price(rows: list[NormalizedRow]) -> None:
    heritage = by_name(rows, "Heritage Table Butter")

    assert heritage.price is None
    assert heritage.unit_price is None


def test_page_level_eta_is_applied_to_every_row(rows: list[NormalizedRow]) -> None:
    assert {r.eta_min for r in rows} == {12}


def test_mapper_accepts_a_bare_site_response(zepto_fixture) -> None:
    """Tolerant to the collector wrapper being absent — the site payload shape is
    the part we do not control."""
    bare = zepto_fixture[0]["search_response"]

    rows = get_mapper("zepto")(bare)

    assert len(rows) == 7
    assert all(r.eta_min is None for r in rows)  # ETA lives on the wrapper only


def test_mapper_is_pure(zepto_fixture) -> None:
    first = get_mapper("zepto")(zepto_fixture)
    second = get_mapper("zepto")(zepto_fixture)

    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]


def test_empty_and_junk_payloads_yield_no_rows() -> None:
    zepto = get_mapper("zepto")

    assert zepto({}) == []
    assert zepto([]) == []
    assert zepto({"layout": []}) == []
    assert zepto("not a payload") == []


@pytest.mark.parametrize("name", ["chaos"])
def test_unimplemented_mappers_refuse_rather_than_return_empty(name: str) -> None:
    assert mapper_is_stub(name)
    with pytest.raises(NotImplementedError):
        MAPPERS[name]({})


@pytest.mark.parametrize("name", ["zepto", "blinkit", "instamart"])
def test_implemented_mappers_are_not_stubs(name: str) -> None:
    assert not mapper_is_stub(name)


# -- resolution ---------------------------------------------------------------
def test_unit_price_handles_volume_and_pieces() -> None:
    assert unit_price(50.0, 500.0, "ml") == (10.0, "per 100 ml")
    assert unit_price(60.0, 6.0, "pc") == (10.0, "per piece")
    assert unit_price(60.0, None, None) == (None, None)


def test_single_source_groups_are_reported_as_unmatched(rows: list[NormalizedRow]) -> None:
    comparison = match({"zepto": rows})

    assert comparison.groups == []  # one universe is not a comparison
    assert len(comparison.unmatched) > 0
    assert all(g.confidence == "unmatched" for g in comparison.unmatched)
    assert comparison.row_count == len(rows)


def test_same_brand_size_and_variant_match_across_universes() -> None:
    zepto = NormalizedRow(
        universe="zepto", name="Amul Salted Butter", brand="Amul",
        variant="salted", qty=500.0, unit="g", price=309.0,
    )
    blinkit = NormalizedRow(
        universe="blinkit", name="Amul Butter (Salted)", brand="Amul",
        variant="salted", qty=0.5, unit="kg", price=305.0,
    )

    comparison = match({"zepto": [zepto], "blinkit": [blinkit]})

    assert len(comparison.groups) == 1
    group = comparison.groups[0]
    assert group.confidence == "close"
    assert group.universes == ["blinkit", "zepto"]
    assert (group.qty, group.unit) == (500.0, "g")  # kg normalised into the same bucket


def test_different_pack_sizes_do_not_get_matched() -> None:
    zepto = NormalizedRow(
        universe="zepto", name="Amul Salted Butter", brand="Amul",
        variant="salted", qty=500.0, unit="g", price=309.0,
    )
    blinkit = NormalizedRow(
        universe="blinkit", name="Amul Salted Butter", brand="Amul",
        variant="salted", qty=100.0, unit="g", price=62.0,
    )

    comparison = match({"zepto": [zepto], "blinkit": [blinkit]})

    assert comparison.groups == []
    assert len(comparison.unmatched) == 2


# -- name agreement: what stops a bucket claiming four products are one --------
def shelf_row(universe: str, name: str, **overrides) -> NormalizedRow:
    base = dict(
        universe=universe, name=name, brand="Amul", qty=100.0, unit="g", price=75.0
    )
    return NormalizedRow(**{**base, **overrides})


def test_same_brand_and_pack_but_different_products_are_not_matched() -> None:
    """The defect that made this check necessary: `amul|100|g` collected garlic
    butter, cheese slices and margarine, and the table presented all three as
    one product priced by three universes."""
    comparison = match(
        {
            "zepto": [shelf_row("zepto", "Amul Garlic & Herbs Butter")],
            "blinkit": [shelf_row("blinkit", "Amul Processed Cheese Slices")],
            "instamart": [shelf_row("instamart", "Amul Delicious Margarine")],
        }
    )

    assert comparison.groups == []
    assert len(comparison.unmatched) == 3
    assert all(len(g.universes) == 1 for g in comparison.unmatched)


def test_the_same_product_named_in_a_different_word_order_still_matches() -> None:
    """The bar is deliberately low enough to survive how three sites write the
    same shelf label."""
    comparison = match(
        {
            "zepto": [shelf_row("zepto", "Amul Butter Pasteurised")],
            "blinkit": [shelf_row("blinkit", "Amul Pasteurised Butter")],
        }
    )

    assert len(comparison.groups) == 1
    assert comparison.groups[0].confidence == "close"
    assert comparison.groups[0].universes == ["blinkit", "zepto"]


def test_pack_wording_and_sizes_inside_a_name_do_not_count_as_agreement() -> None:
    """"Pack of 2", "100 g" and the brand itself are all either filler or already
    in the group key. Counting them would make two unrelated products of the same
    size look alike."""
    comparison = match(
        {
            "zepto": [shelf_row("zepto", "Amul Cheese Cubes 100 g (Pack of 2)")],
            "blinkit": [shelf_row("blinkit", "Amul Paneer 100 g (Pack of 2)")],
        }
    )

    assert comparison.groups == []
    assert len(comparison.unmatched) == 2


def test_agreement_is_checked_against_every_member_not_just_the_first() -> None:
    """A agreeing with B and B with C does not make A agree with C.

    Rows are placed in universe order (blinkit, instamart, zepto). "Amul Butter"
    goes first; "Amul Cooking Butter" agrees with it and joins; "Amul Butter
    Chiplet" agrees with "Amul Butter" too, but NOT with "Amul Cooking Butter" —
    so it has to start its own subgroup. Checking only the first member would
    chain cooking butter and butter chiplets into one comparison row, which is
    the shape of the bug this whole check exists to prevent.
    """
    comparison = match(
        {
            "blinkit": [shelf_row("blinkit", "Amul Butter")],
            "instamart": [shelf_row("instamart", "Amul Cooking Butter")],
            "zepto": [shelf_row("zepto", "Amul Butter Chiplet")],
        }
    )
    names_per_group = [{r.name for r in g.rows} for g in comparison.groups + comparison.unmatched]

    chained = next(n for n in names_per_group if "Amul Cooking Butter" in n)
    assert "Amul Butter Chiplet" not in chained
    # ...and the pair that DOES agree all round is still matched, so the check
    # is cutting the chain rather than refusing everything.
    assert chained == {"Amul Butter", "Amul Cooking Butter"}


def test_an_unparseable_pack_size_is_never_matched_across_universes() -> None:
    """Two nulls are not a match. A row whose size could not be read is reported
    as single-source however well its name agrees with another universe's."""
    comparison = match(
        {
            "zepto": [shelf_row("zepto", "Amul Butter Combo", qty=None, unit=None)],
            "blinkit": [shelf_row("blinkit", "Amul Butter Combo", qty=None, unit=None)],
        }
    )

    assert comparison.groups == []
    assert len(comparison.unmatched) == 2
    assert all(len(g.universes) == 1 for g in comparison.unmatched)


def test_no_group_is_ever_labelled_more_confidently_than_close() -> None:
    """The resolver compares a brand token, a pack size, a variant and the words
    in two names. `close` is the strongest thing that can honestly say."""
    comparison = match(
        {
            "zepto": [shelf_row("zepto", "Amul Pasteurised Butter")],
            "blinkit": [shelf_row("blinkit", "Amul Pasteurised Butter")],
        }
    )

    labels = {g.confidence for g in comparison.groups + comparison.unmatched}

    assert labels <= {"close", "unmatched"}
    assert comparison.groups[0].confidence == "close"


# -- one malformed widget must not take the universe down ---------------------
def test_a_widget_with_a_null_resolver_is_skipped_not_fatal() -> None:
    """`.get("resolver", {})` returns None, not {}, when the key is present and
    explicitly null. The AttributeError that followed took the whole universe
    down over one malformed widget on a page whose other widgets were fine —
    and a crashed universe reports `failed`, so a page that mostly parsed came
    back as nothing at all."""
    payload = {
        "layout": [
            {"data": {"resolver": None}},
            {"data": {"resolver": {"data": None}}},
            {"data": {"resolver": {"data": {"items": None}}}},
            {"data": None},
            {
                "data": {
                    "resolver": {
                        "data": {
                            "items": [
                                {
                                    "product": {"name": "Amul Salted Butter", "brand": "Amul"},
                                    "productVariant": {"id": "v1"},
                                    "sellingPrice": 30900,
                                }
                            ]
                        }
                    }
                }
            },
        ]
    }

    rows = get_mapper("zepto")(payload)

    assert [r.name for r in rows] == ["Amul Salted Butter"]
    assert rows[0].price == 309.0
