"""Blinkit mapper — two input shapes, one output.

1. **Collector rows** (the live path). The published Blinkit collector emits the
   same flattened 18-field row as the Zepto one, so detection and mapping are
   the shared ones in `mappers/collector_rows.py`. Prices are already RUPEES —
   the paise division is a Zepto-only quirk and must never be applied here.

2. **Raw search response** (the fallback). A wrapper record carrying the site's
   own `POST /v1/layout/search` body, which is what the fixtures and any offline
   capture use.

`map_blinkit` picks the branch by looking for `product_name` + `selling_price`.

Payload facts the raw-response branch relies on were confirmed against two saved
captures. Location values in the public fixtures are synthetic:
  * The body is `{"is_success": true, "response": {"snippets": [...]}}`. A bare
    `{"snippets": [...]}` root is accepted too.
  * Product cards are `snippets[]` entries whose `widget_type` starts with
    `product_card_snippet` (`..._type_2` today). Other widget types in the same
    list — the `image_text_vr_type_header` heading, a `grid_container_vr` holding
    a "Similar brands" carousel — are NOT products and are skipped by type, not
    by position. Containers are still walked, so a product card nested inside one
    would be found; in both captures none was.
  * Money is a display STRING under `mrp.text` / `normal_price.text` ("₹63"),
    with no paise field. `atc_action.add_to_cart.cart_item` carries the same
    numbers as real NUMBERS (`price`, `mrp`) plus `unit`, `inventory` and
    `brand`, so it is preferred and the display strings are the fallback.
  * `mrp` is frequently absent at the card level for an undiscounted product,
    while the site's own `cart_item.mrp` equals the selling price. We report what
    `cart_item` says and never invent an MRP the payload does not carry.
  * Stock: `product_state == "out_of_stock"` or `inventory == 0`. `is_sold_out`
    was `false` on every out-of-stock card in the captures, so it is read but
    never trusted alone.
  * Sponsored: an `overlay_badges[]` image whose URL contains `assets/ui/ad`, or
    an `ads_campaign_id` anywhere in the snippet's `tracking`. The rank-1 card is
    frequently a paid substitute for the searched product; it is kept and marked
    `sponsored=True` so resolution can de-emphasise it, never silently dropped.
  * ETA is PER CARD here, not page-level: a `product_badges[]` entry of
    `type == "ETA"` carries it in its text ("8 mins") or its icon URL
    (`.../15-mins.png`), with `eta_tag.image.url` as a third source. The
    wrapper's page-level `eta_minutes` is the fallback.
  * `merchant_id` is per card and NOT constant — an "express" dark store and a
    "longtail" warehouse can both appear in one response. The location proof
    therefore takes a SET of allowed store ids per pincode
    (`SVERSE_BLINKIT_STORE_MAP`), and the mapper reports rows from every card
    rather than guessing which merchant is the real one.
  * `rating` has no home on `NormalizedRow` and is dropped rather than
    half-modelled.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from ..resolve import NormalizedRow, unit_price
from . import as_records, site_payload, unwrap_record
from .collector_rows import (
    is_collector_row,
    map_collector_rows,
    number,
    parse_packsize,
    variant_label,
)
from .collector_rows import text as _text

# Widget types that carry a product. Matched as a prefix so a `_type_3` rename
# does not silently drop every row.
PRODUCT_CARD_PREFIX = "product_card_snippet"

# How deep the snippet walk goes looking for nested product cards. The captures
# nest containers two levels; four is headroom, not an invitation.
_MAX_DEPTH = 6

_MINUTES_RE = re.compile(r"(\d+)\s*-?\s*min", re.IGNORECASE)
_MONEY_RE = re.compile(r"[0-9][0-9,]*(?:\.\d+)?")

_AD_BADGE_MARKER = "assets/ui/ad"
_AD_TRACKING_MARKER = "ads_campaign_id"


def _rupees_text(value: Any) -> float | None:
    """'₹63' -> 63.0; '₹1,234.50' -> 1234.5. Already-numeric values pass through."""
    parsed = number(value)
    if parsed is not None:
        return round(parsed, 2) if parsed > 0 else None
    if not isinstance(value, str):
        return None
    match = _MONEY_RE.search(value)
    if not match:
        return None
    try:
        amount = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    return round(amount, 2) if amount > 0 else None


def _labelled(node: Any) -> str | None:
    """Blinkit wraps most display text as `{"text": "...", "font": ...}`."""
    if isinstance(node, dict):
        return _text(node.get("text"))
    return _text(node)


def _price_text(node: Any) -> float | None:
    return _rupees_text(node.get("text")) if isinstance(node, dict) else _rupees_text(node)


def _cart_item(data: dict[str, Any]) -> dict[str, Any]:
    """`atc_action.add_to_cart.cart_item` — the cleanest numbers on the card."""
    atc = data.get("atc_action")
    add = atc.get("add_to_cart") if isinstance(atc, dict) else None
    item = add.get("cart_item") if isinstance(add, dict) else None
    return item if isinstance(item, dict) else {}


def _cards(node: Any, path: str, depth: int = 0) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield (snippet, json-ish path) for every product card under `node`.

    Walks containers rather than assuming products are always top-level
    snippets, and keys off `widget_type` so a heading or a brand carousel can
    never be mistaken for a product.
    """
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, dict):
        widget_type = node.get("widget_type")
        if (
            isinstance(widget_type, str)
            and widget_type.startswith(PRODUCT_CARD_PREFIX)
            and isinstance(node.get("data"), dict)
        ):
            yield node, path
            return
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                yield from _cards(value, f"{path}.{key}", depth + 1)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _cards(value, f"{path}[{index}]", depth + 1)


def _eta_minutes(data: dict[str, Any]) -> int | None:
    """Per-card ETA, from the badge text, the badge icon, or the eta tag icon."""
    badges = data.get("product_badges")
    if isinstance(badges, list):
        for badge in badges:
            if not isinstance(badge, dict) or badge.get("type") != "ETA":
                continue
            text_data = badge.get("text_data")
            image_data = badge.get("image_data")
            for candidate in (
                _labelled(text_data),
                (image_data or {}).get("url") if isinstance(image_data, dict) else None,
            ):
                match = _MINUTES_RE.search(candidate) if isinstance(candidate, str) else None
                if match:
                    return int(match.group(1))

    eta_tag = data.get("eta_tag")
    image = eta_tag.get("image") if isinstance(eta_tag, dict) else None
    url = image.get("url") if isinstance(image, dict) else None
    match = _MINUTES_RE.search(url) if isinstance(url, str) else None
    return int(match.group(1)) if match else None


def _is_sponsored(snippet: dict[str, Any], data: dict[str, Any]) -> bool:
    badges = data.get("overlay_badges")
    if isinstance(badges, list):
        for badge in badges:
            image = badge.get("image") if isinstance(badge, dict) else None
            url = image.get("url") if isinstance(image, dict) else None
            if isinstance(url, str) and _AD_BADGE_MARKER in url:
                return True

    tracking = snippet.get("tracking")
    if isinstance(tracking, (dict, list)):
        try:
            return _AD_TRACKING_MARKER in json.dumps(tracking)
        except (TypeError, ValueError):
            return False
    return False


def _product_id(data: dict[str, Any], cart: dict[str, Any]) -> str | None:
    identity = data.get("identity")
    candidates = (
        identity.get("id") if isinstance(identity, dict) else None,
        data.get("product_id"),
        (data.get("meta") or {}).get("product_id") if isinstance(data.get("meta"), dict) else None,
        cart.get("product_id"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return str(candidate)
    return None


def _root(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(collector record, the site response root that holds `snippets`)."""
    record = unwrap_record(payload)
    body = site_payload(record, "response") or record
    nested = body.get("response") if isinstance(body, dict) else None
    root = nested if isinstance(nested, dict) else body
    return record, root if isinstance(root, dict) else {}


def map_blinkit(payload: Any) -> list[NormalizedRow]:
    # branch 1: collector rows (live)
    collector_rows = [record for record in as_records(payload) if is_collector_row(record)]
    if collector_rows:
        return map_collector_rows(collector_rows, "blinkit")

    # branch 2: raw search response (fixtures, offline captures)
    record, root = _root(payload)
    if root.get("is_success") is False:
        return []

    # Page-level facts live on the wrapper; the per-card ETA wins where present.
    page_eta = number(record.get("eta_minutes"))
    page_eta = int(page_eta) if page_eta is not None else None
    captured_at = _text(record.get("captured_at"))
    resolved_area = _text(record.get("resolved_area"))

    snippets = root.get("snippets")
    if not isinstance(snippets, list):
        return []

    rows: list[NormalizedRow] = []
    seen: set[str] = set()
    for snippet, path in _cards(snippets, "snippets"):
        data = snippet["data"]
        cart = _cart_item(data)

        name = _labelled(data.get("name")) or _text(cart.get("product_name")) or _labelled(
            data.get("display_name")
        )
        if not name:
            continue

        product_id = _product_id(data, cart)
        dedupe_key = product_id or name
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        qty, unit = parse_packsize(_labelled(data.get("variant")) or _text(cart.get("unit")))

        # cart_item carries real numbers; the card carries "₹63" display strings.
        price = _rupees_text(cart.get("price"))
        if price is None:
            price = _price_text(data.get("normal_price"))
        mrp = _rupees_text(cart.get("mrp"))
        if mrp is None:
            mrp = _price_text(data.get("mrp"))
        up, basis = unit_price(price, qty, unit)

        available = number(data.get("inventory"))
        if available is None:
            available = number(cart.get("inventory"))
        available = int(available) if available is not None else None
        out_of_stock = (
            data.get("product_state") == "out_of_stock"
            or data.get("is_sold_out") is True
            or available == 0
        )

        eta = _eta_minutes(data)

        image = data.get("image")
        image_url = _text(image.get("url")) if isinstance(image, dict) else _text(image)

        rows.append(
            NormalizedRow(
                universe="blinkit",
                name=name,
                brand=_labelled(data.get("brand_name")) or _text(cart.get("brand")),
                variant=variant_label(name),
                qty=qty,
                unit=unit,
                price=price,
                mrp=mrp,
                unit_price=up,
                unit_price_basis=basis,
                in_stock=not out_of_stock,
                qty_available=available,
                eta_min=eta if eta is not None else page_eta,
                sponsored=_is_sponsored(snippet, data),
                product_id=product_id,
                image_url=image_url or _text(cart.get("image_url")),
                raw_ref=path,
                captured_at=captured_at,
                resolved_area=resolved_area,
            )
        )

    return rows
