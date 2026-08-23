"""The flattened collector-row contract, shared by every published collector.

Our Scraper Studio collectors all emit the same 18-field row through
`GET /dca/dataset` — ONE RECORD PER PRODUCT, already flattened and already in
RUPEES:

    product_name, brand, package_size ("1 pack (100 g)", "100 g"), product_id,
    mrp, selling_price, discounted_selling_price,          # rupees, NOT paise
    out_of_stock (bool), available_quantity (nullable int),
    is_sponsored (bool), rating (nullable), image_url,
    serp_screenshot (Bright-Data file reference),
    store_id (nullable), requested_pincode, resolved_area (nullable),
    eta_minutes (nullable int),
    captured_at (ISO8601 UTC, stamped at parse time on every row)

Zepto and Blinkit both emit exactly this, so the row -> `NormalizedRow` mapping
lives here once rather than being copied per universe. `rating` has no home on
`NormalizedRow` and is deliberately dropped rather than half-modelled.

Pure: same records in, same rows out. No network, no clock, no LLM.

Two page-level facts fall back across rows because they really are page-level and
the collector does not always stamp every row: `eta_minutes` and the SERP capture.
`resolved_area` deliberately does NOT — it is each row's own proof of where its
price came from, and `captured_at` is each row's own provenance. Borrowing either
from a neighbour manufactures evidence.
"""

from __future__ import annotations

import re
from typing import Any

from ..resolve import NormalizedRow, unit_price

_UNITS = r"kg|g|gm|gms|grams?|ml|l|ltr|litres?|pcs?|pieces?"

_PACKSIZE_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*({_UNITS})\b", re.IGNORECASE)

# Multipacks. The collector contract has no numeric pack fields, only the
# display string, so "1 pack (10 x 10 g)" has to resolve to 100 g rather than
# 10 g or the unit price is wrong by an order of magnitude. Both orderings show
# up in real captures ("1 pack (10 x 10 g)", "100 g X 2") and the raw payload
# confirmed the arithmetic: both of those carry a numeric packsize of 100 g and
# 200 g respectively.
_MULTIPACK_COUNT_FIRST = re.compile(
    rf"(\d+)\s*[x×]\s*(\d+(?:\.\d+)?)\s*({_UNITS})\b", re.IGNORECASE
)
_MULTIPACK_SIZE_FIRST = re.compile(
    rf"(\d+(?:\.\d+)?)\s*({_UNITS})\s*[x×]\s*(\d+)\b", re.IGNORECASE
)

_PACKSIZE_UNITS = {
    "kg": "kg", "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "ml": "ml", "l": "l", "ltr": "l", "litre": "l", "litres": "l",
    "pc": "pc", "pcs": "pc", "piece": "pc", "pieces": "pc",
}

# Presence of both marks a flattened per-product record rather than a wrapper.
COLLECTOR_ROW_MARKERS = ("product_name", "selling_price")


def is_collector_row(record: Any) -> bool:
    return isinstance(record, dict) and all(key in record for key in COLLECTOR_ROW_MARKERS)


def text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def number(value: Any) -> float | None:
    """Numbers only — `bool` is an `int` in Python and must not slip through."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def rupees(value: Any) -> float | None:
    if isinstance(value, dict):
        # Live delivery serializes Studio Money as {"value": 65, "currency": "INR", "symbol": "₹"}
        value = value.get("value", value.get("amount"))
    parsed = number(value)
    return round(parsed, 2) if parsed is not None and parsed > 0 else None


def parse_packsize(raw: str | None) -> tuple[float | None, str | None]:
    """'1 pack (500 g)' -> (500.0, 'g'); '1 pack (10 x 10 g)' -> (100.0, 'g').

    The only size signal on the collector-row path. Where a raw site payload
    carries numeric pack fields those win and this is the fallback.
    """
    if not raw:
        return None, None

    for pattern, count_first in ((_MULTIPACK_COUNT_FIRST, True), (_MULTIPACK_SIZE_FIRST, False)):
        matches = pattern.findall(raw)
        if not matches:
            continue
        if count_first:
            count_raw, size_raw, unit_raw = matches[-1]
        else:
            size_raw, unit_raw, count_raw = matches[-1]
        unit = _PACKSIZE_UNITS.get(unit_raw.lower())
        if unit is None:
            continue
        try:
            total = float(size_raw) * float(count_raw)
        except ValueError:
            continue
        return round(total, 4), unit

    matches = _PACKSIZE_RE.findall(raw)
    if not matches:
        return None, None
    qty_raw, unit_raw = matches[-1]
    unit = _PACKSIZE_UNITS.get(unit_raw.lower())
    if unit is None:
        return None, None
    try:
        return float(qty_raw), unit
    except ValueError:
        return None, None


# "unsalted", "un-salted", "un salted", "un_salted" — the sites all spell it
# differently, and a substring test for "salted" matches every one of them. That
# put unsalted butter in the SALTED bucket of the comparison, which is a wrong
# match presented as a match. The negative has to be checked first, and it has to
# tolerate the separator.
_UNSALTED_RE = re.compile(r"\bun[\s._-]*salted\b", re.IGNORECASE)
_SALTED_RE = re.compile(r"\bsalted\b", re.IGNORECASE)


def variant_label(name: str) -> str | None:
    """Only the distinction DESIGN.md §6 asks for. Anything richer would start
    inventing product taxonomy the payload does not state."""
    if _UNSALTED_RE.search(name):
        return "unsalted"
    if _SALTED_RE.search(name):
        return "salted"
    return None


def shelf_price_rupees(record: dict[str, Any]) -> float | None:
    """The price a logged-out shopper sees. Already rupees — nothing is divided
    by 100 here. The contract carries no member-price field at all, which is the
    right shape: there is nothing on this path that could leak a Zepto Pass or
    any other tier price."""
    candidates = [rupees(record.get("discounted_selling_price")), rupees(record.get("selling_price"))]
    values = [value for value in candidates if value is not None]
    return min(values) if values else None


def dataset_eta(records: list[dict[str, Any]]) -> int | None:
    """ETA is a page-level fact stamped onto per-product rows.

    The collector stamps every row, but if it ever stamps only some, the first
    value found stands for the dataset rather than leaving most rows blank. Same
    treatment as the SERP capture. Nothing is invented: no ETA anywhere means no
    ETA on any row.
    """
    for record in records:
        eta = number(record.get("eta_minutes"))
        if eta is not None:
            return int(eta)
    return None


def map_collector_rows(records: list[dict[str, Any]], universe: str) -> list[NormalizedRow]:
    """Flattened dataset rows -> `NormalizedRow`s, for any universe."""
    fallback_eta = dataset_eta(records)
    rows: list[NormalizedRow] = []
    seen_product_ids: set[str] = set()
    for index, record in enumerate(records):
        name = text(record.get("product_name"))
        if not name:
            continue

        # One product, one row. A collector that merges two result pages (Blinkit
        # does) can deliver the same product twice, and a duplicate is not a
        # second shelf listing — it would be counted twice in `rows_kept`, shown
        # twice in the comparison cell, and would make one universe look like it
        # stocked more than it does.
        product_id = text(record.get("product_id"))
        if product_id is not None:
            if product_id in seen_product_ids:
                continue
            seen_product_ids.add(product_id)

        qty, unit = parse_packsize(text(record.get("package_size")))
        price = shelf_price_rupees(record)
        up, basis = unit_price(price, qty, unit)

        available = number(record.get("available_quantity"))
        available = int(available) if available is not None else None
        in_stock = not bool(record.get("out_of_stock")) and (available is None or available > 0)

        eta = number(record.get("eta_minutes"))

        rows.append(
            NormalizedRow(
                universe=universe,
                name=name,
                brand=text(record.get("brand")),
                variant=variant_label(name),
                qty=qty,
                unit=unit,
                price=price,
                mrp=rupees(record.get("mrp")),
                unit_price=up,
                unit_price_basis=basis,
                in_stock=in_stock,
                qty_available=available,
                eta_min=int(eta) if eta is not None else fallback_eta,
                sponsored=bool(record.get("is_sponsored")),
                image_url=text(record.get("image_url")),
                product_id=product_id,
                raw_ref=f"dataset[{index}]",
                captured_at=text(record.get("captured_at")),
                # NO cross-row backfill. `resolved_area` is this row's own proof
                # of where its price came from; borrowing another row's would let
                # one located row vouch for rows the site never located, which is
                # the precise failure the fail-closed location proof exists to
                # catch. `captured_at` is left alone for the same reason.
                resolved_area=text(record.get("resolved_area")),
            )
        )
    return rows
