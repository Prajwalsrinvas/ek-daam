"""Instamart mapper — two input shapes, one output.

1. **Collector rows** (the live path). The published Instamart collector emits
   the same flattened 18-field row as the Zepto and Blinkit ones, so detection
   and mapping are the shared ones in `mappers/collector_rows.py`. Prices are
   already RUPEES — the paise division is a Zepto-only quirk.

2. **Raw search response** (the fallback). A wrapper record carrying the site's
   own `POST /api/instamart/search/v2` body, which is what the fixtures and any
   offline capture use.

`map_instamart` picks the branch by looking for `product_name` + `selling_price`.

Payload facts the raw-response branch relies on were confirmed against a saved
capture with 32 items, 72 variations, and one pod. Location values in the public
fixtures are synthetic:
  * The body is `{"data": {"cards": [...]}, "statusCode": 0}`. A bare
    `{"cards": [...]}` root is accepted too.
  * Products live at `cards[].card.card.gridElements.infoWithStyle.items[]`. A
    card without `gridElements` — the `InlineViewFilterSortWidget` sitting in
    the same list — is not a product card and yields nothing.
  * **One row per VARIATION, not per item.** Each item is a product and each
    variation is one purchasable pack of it, with its own `skuId` and price.
    Deduped on `skuId` (`spinId`, then name+size, as fallbacks).
  * Money is a protobuf-style `{units, nanos}` object and `units` arrives as a
    STRING (`"252"`): rupees = float(units) + nanos / 1e9. `offerPrice` is what
    the shopper pays and `mrp` the struck-through price; where `offerPrice` is
    absent the site shows the MRP, so that is the fallback.
  * `unitLevelPrice` ("63/100 g") is the site's own per-unit figure and is
    PREFERRED over recomputing. It is present on 49 of the 72 variations and, on
    every one of those, agrees with our own arithmetic to the paisa — so the
    fallback is a genuine fallback, not a second opinion.
  * `weightInGrams` is present and correct on every variation, and it is
    deliberately NOT used as the quantity. It reports 200/100/520/800/450 g for
    the five `"1 Combo"` rows — a butter-plus-bread bundle whose total mass is
    not a comparable pack size — and taking it would invent a per-100 g basis
    for a product that has none. The site agrees: every combo's
    `unitLevelPrice` is empty. Quantity comes from `quantityDescription` through
    the shared `parse_packsize` on BOTH branches, so the two shapes cannot
    disagree about what a row weighs.
  * **Multipacks resolve to the TOTAL**: `"100 g x 4"` is 400 g, not 100 g.
    Three independent confirmations in the capture: the site's own
    `weightInGrams` is 400; its own `unitLevelPrice` is "63/100 g"; and the
    price is exactly 4x the single pack (252 vs 63). `NormalizedRow` has no
    pack-count field and the count is NOT smuggled into `variant` — that field
    feeds `group_key`, where "unsalted x4" would silently stop a real
    cross-universe match. The pack structure therefore stays in the source
    `package_size`; qty is the honest total, which is also what makes the unit
    price come out right.
  * Stock: the item's `inStock`/`isAvail` and the variation's
    `inventory.inStock` / `slotInfo.isAvail`. There is no stock COUNT in the
    payload — `cartAllowedQuantity` is a per-order cap ("Only 1 unit(s) ... per
    order"), not inventory — so `qty_available` stays None unless
    `lowStockText` names a number. All 72 sample variations have it empty.
  * Sponsored: an item `badges[]` entry of type `BADGE_TYPE_AD` (or text "Ad"),
    or a non-empty `adTrackingContext`. Both agree on exactly the same 4 items
    in the capture. Matched on an `_AD` SUFFIX, never the substring "AD":
    "BADGE" itself contains it, and `BADGE_TYPE_INSTA_UPGRADE` is a different
    badge on a non-sponsored item. Sponsorship is a property of the ITEM and
    applies to all of its variations.
  * ETA: item-level `sla` is null on every variation in the capture, so the real
    source is the per-pod delivery SLA under
    `configs.IM_PAGE_CONFIGS.configInfo[].card.podDetailsList[]`, keyed by
    `podId`. The wrapper's page-level `eta_minutes` is the last fallback.
  * `podId` is the location proof — Instamart's answer to Blinkit's
    `merchant_id` and Zepto's store UUID. Every row carries it.
  * `rating` has no home on `NormalizedRow` and is dropped rather than
    half-modelled, exactly as on the other two universes.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ..resolve import NormalizedRow, unit_price
from . import as_records, unwrap_record
from .collector_rows import (
    is_collector_row,
    map_collector_rows,
    number,
    parse_packsize,
    variant_label,
)
from .collector_rows import text as _text

# "63/100 g" / "12.5/ml" -> (amount, qty, unit). A missing qty means "per one".
_UNIT_LEVEL_RE = re.compile(r"([0-9][0-9,]*(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)?\s*([A-Za-z]+)")

_LOW_STOCK_RE = re.compile(r"(\d+)")

# Where Instamart's own image ids resolve when the payload carries no absolute
# URL (the capture carries only ids).
_IMAGE_CDN = "https://media-assets.swiggy.com/swiggy/image/upload/"


def _rupees(node: Any) -> float | None:
    """`{"units": "252", "nanos": 0}` -> 252.0. `units` is a STRING on the wire."""
    if isinstance(node, dict):
        units = node.get("units")
        nanos = node.get("nanos")
        if units is None or units == "":
            base = 0.0
        else:
            try:
                base = float(str(units))
            except (TypeError, ValueError):
                return None
        offset = (
            nanos / 1e9
            if isinstance(nanos, (int, float)) and not isinstance(nanos, bool)
            else 0.0
        )
        amount = base + offset
    else:
        parsed = number(node)
        if parsed is None:
            return None
        amount = parsed
    amount = round(amount, 2)
    return amount if amount > 0 else None


def _site_unit_price(raw: Any) -> tuple[float | None, str | None]:
    """The site's own `unitLevelPrice`, normalised onto our per-100 basis.

    "63/100 g" is a price of 63 for 100 g, so it goes through exactly the same
    `unit_price` arithmetic as a pack — there is no second, parallel notion of a
    basis that could drift from the one the rest of the app uses.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    match = _UNIT_LEVEL_RE.search(raw)
    if not match:
        return None, None
    amount_raw, qty_raw, unit_raw = match.groups()
    try:
        amount = float(amount_raw.replace(",", ""))
        qty = float(qty_raw) if qty_raw else 1.0
    except ValueError:
        return None, None
    return unit_price(amount, qty, unit_raw)


def _cards_root(node: Any) -> dict[str, Any]:
    """A node — or its `data` child — that actually carries `cards[]`."""
    if not isinstance(node, dict):
        return {}
    if isinstance(node.get("cards"), list):
        return node
    data = node.get("data")
    if isinstance(data, dict) and isinstance(data.get("cards"), list):
        return data
    return {}


def _root(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(collector record, the site response root that holds `cards`)."""
    record = unwrap_record(payload)
    for candidate in (record, record.get("search_response"), record.get("data")):
        root = _cards_root(candidate)
        if root:
            return record, root
    return record, {}


def _items(root: dict[str, Any]) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield (item, json-ish path) for every product item in the grid cards."""
    cards = root.get("cards")
    if not isinstance(cards, list):
        return
    for card_index, card in enumerate(cards):
        inner = card.get("card") if isinstance(card, dict) else None
        inner = inner.get("card") if isinstance(inner, dict) else None
        grid = inner.get("gridElements") if isinstance(inner, dict) else None
        info = grid.get("infoWithStyle") if isinstance(grid, dict) else None
        items = info.get("items") if isinstance(info, dict) else None
        if not isinstance(items, list):
            continue  # the filter/sort widget, or any other non-product card
        for item_index, item in enumerate(items):
            if isinstance(item, dict):
                yield item, f"cards[{card_index}].items[{item_index}]"


def _pod_eta(root: dict[str, Any]) -> dict[str, int]:
    """podId -> delivery SLA in minutes, from the page config block."""
    out: dict[str, int] = {}
    configs = root.get("configs")
    page = configs.get("IM_PAGE_CONFIGS") if isinstance(configs, dict) else None
    entries = page.get("configInfo") if isinstance(page, dict) else None
    if not isinstance(entries, list):
        return out
    for entry in entries:
        card = entry.get("card") if isinstance(entry, dict) else None
        pods = card.get("podDetailsList") if isinstance(card, dict) else None
        if not isinstance(pods, list):
            continue
        for pod in pods:
            if not isinstance(pod, dict):
                continue
            details = pod.get("serviceabilityDetails")
            sla = details.get("sla") if isinstance(details, dict) else None
            value = sla.get("value") if isinstance(sla, dict) else None
            pod_id = pod.get("podId")
            if pod_id is None or value is None:
                continue
            try:
                out[str(pod_id)] = int(str(value))
            except (TypeError, ValueError):
                continue
    return out


def _is_sponsored(item: dict[str, Any]) -> bool:
    """An item-level fact: every variation of a promoted product is promoted."""
    badges = item.get("badges")
    if isinstance(badges, list):
        for badge in badges:
            if not isinstance(badge, dict):
                continue
            # `_AD` as a SUFFIX. "BADGE" contains "AD", and
            # BADGE_TYPE_INSTA_UPGRADE is a different badge that sits on a
            # non-sponsored item.
            badge_type = badge.get("type")
            if isinstance(badge_type, str) and badge_type.upper().endswith("_AD"):
                return True
            label = badge.get("text")
            if isinstance(label, str) and label.strip().lower() == "ad":
                return True
    tracking = item.get("adTrackingContext")
    return isinstance(tracking, str) and bool(tracking.strip())


def _out_of_stock(item: dict[str, Any], variation: dict[str, Any]) -> bool:
    if item.get("inStock") is False or item.get("isAvail") is False:
        return True
    inventory = variation.get("inventory")
    if isinstance(inventory, dict) and inventory.get("inStock") is False:
        return True
    slot = variation.get("slotInfo")
    return isinstance(slot, dict) and slot.get("isAvail") is False


def _qty_available(variation: dict[str, Any]) -> int | None:
    """No stock count exists in the payload. `lowStockText` sometimes names one
    ("Only 2 left"); `cartAllowedQuantity` is an ORDER CAP and is never read."""
    inventory = variation.get("inventory")
    label = inventory.get("lowStockText") if isinstance(inventory, dict) else None
    match = _LOW_STOCK_RE.search(label) if isinstance(label, str) else None
    return int(match.group(1)) if match else None


def _variation_eta(
    variation: dict[str, Any], pod_eta: dict[str, int], page_eta: int | None
) -> int | None:
    sla = variation.get("sla")
    if isinstance(sla, dict):
        for candidate in (sla.get("value"), sla.get("deliveryTime")):
            if candidate is None:
                continue
            try:
                return int(str(candidate))
            except (TypeError, ValueError):
                continue
    pod_id = variation.get("podId")
    if pod_id is not None and str(pod_id) in pod_eta:
        return pod_eta[str(pod_id)]
    return page_eta


def _image_url(variation: dict[str, Any]) -> str | None:
    """`imageIds` are CDN paths, not URLs. An absolute one passes through."""
    ids = variation.get("imageIds")
    if not isinstance(ids, list):
        return None
    for image_id in ids:
        found = _text(image_id)
        if not found:
            continue
        return found if found.startswith(("http://", "https://")) else _IMAGE_CDN + found
    return None


def _dedupe_key(variation: dict[str, Any], item: dict[str, Any]) -> str:
    for key in ("skuId", "spinId"):
        value = _text(variation.get(key))
        if value:
            return value
    name = _text(variation.get("displayName")) or _text(item.get("displayName")) or ""
    return f"{name}|{_text(variation.get('quantityDescription')) or ''}"


def map_instamart(payload: Any) -> list[NormalizedRow]:
    # branch 1: collector rows (live)
    collector_rows = [record for record in as_records(payload) if is_collector_row(record)]
    if collector_rows:
        return map_collector_rows(collector_rows, "instamart")

    # branch 2: raw search response (fixtures, offline captures)
    record, root = _root(payload)
    if not root:
        return []

    page_eta = number(record.get("eta_minutes"))
    page_eta = int(page_eta) if page_eta is not None else None
    captured_at = _text(record.get("captured_at"))
    resolved_area = _text(record.get("resolved_area"))
    pod_eta = _pod_eta(root)

    rows: list[NormalizedRow] = []
    seen: set[str] = set()
    for item, path in _items(root):
        variations = item.get("variations")
        if not isinstance(variations, list):
            continue
        sponsored = _is_sponsored(item)

        for index, variation in enumerate(variations):
            if not isinstance(variation, dict):
                continue

            name = _text(variation.get("displayName")) or _text(item.get("displayName"))
            if not name:
                continue

            key = _dedupe_key(variation, item)
            if key in seen:
                continue
            seen.add(key)

            qty, unit = parse_packsize(_text(variation.get("quantityDescription")))

            price_node = variation.get("price")
            price_node = price_node if isinstance(price_node, dict) else {}
            mrp = _rupees(price_node.get("mrp"))
            price = _rupees(price_node.get("offerPrice"))
            if price is None:
                # No offer price means the shopper pays the MRP, which is what
                # the site displays. Nothing is invented either way.
                price = mrp

            # The site's own per-unit figure wins; ours is the fallback.
            up, basis = _site_unit_price(price_node.get("unitLevelPrice"))
            if up is None:
                up, basis = unit_price(price, qty, unit)

            rows.append(
                NormalizedRow(
                    universe="instamart",
                    name=name,
                    brand=_text(variation.get("brandName")) or _text(item.get("brand")),
                    variant=variant_label(name),
                    qty=qty,
                    unit=unit,
                    price=price,
                    mrp=mrp,
                    unit_price=up,
                    unit_price_basis=basis,
                    in_stock=not _out_of_stock(item, variation),
                    qty_available=_qty_available(variation),
                    eta_min=_variation_eta(variation, pod_eta, page_eta),
                    sponsored=sponsored,
                    product_id=_text(variation.get("skuId")) or _text(variation.get("spinId")),
                    image_url=_image_url(variation),
                    raw_ref=f"{path}.variations[{index}]",
                    captured_at=captured_at,
                    resolved_area=resolved_area,
                )
            )

    return rows
