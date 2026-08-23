"""Zepto mapper — two input shapes, one output.

1. **Collector rows** (the live path). The published collector emits ONE RECORD
   PER PRODUCT through `GET /dca/dataset`, already flattened and already in
   RUPEES. That 18-field contract is shared with every other published collector
   and both its detection and its mapping live in `mappers/collector_rows.py`.

2. **Raw search response** (the fallback). A wrapper record carrying the site's
   own `POST /user-search-service/api/v3/search` body, which is what the mock
   fixture and any offline capture use.

`map_zepto` picks the branch by looking for `product_name` + `selling_price`.

CONFIRMED against the first real dataset output (see `tests/fixtures/README.md`):
snake_case key casing, Money-object prices already in rupees, ISO8601
`captured_at`. `serp_screenshot` arrives as a Bright-Data file reference rather
than a URL, and `mappers.extract_screenshot_url` yields no screenshot rather
than crashing on it.

Payload facts the raw-response branch relies on:
  * Products live under `layout[].data.resolver.data.items[]`. Grid widgets wrap
    each product in `productResponse`; the sponsored-ads widget nests them one
    level deeper as `items[].items[]` entries of `type == "PRODUCT_ITEM"` whose
    `data` *is* the product node. Rather than encode both shapes twice, we walk
    each widget and take any dict that carries both `sellingPrice` and `product`.
  * ALL prices are in PAISE. Divide by 100.
  * `zeptoPassPrice` and `superSaverSellingPrice` are member/tier prices.
    Reporting them would be a product-rule violation (DESIGN.md non-goals), so
    they are never read here. Only `sellingPrice`, `discountedSellingPrice` and
    `mrp` are.
  * ETA is page-level on Zepto, not per product, so it comes off the collector
    record rather than the product node.
"""

from __future__ import annotations

from typing import Any, Iterator

from ..resolve import NormalizedRow, unit_price
from . import as_records, site_payload, unwrap_record
from .collector_rows import (
    is_collector_row,
    map_collector_rows,
    parse_packsize,
    variant_label,
)
from .collector_rows import text as _text

# Prices arrive as integer paise.
PAISE = 100.0

# Zepto's own unit vocabulary -> the canonical short units used everywhere else.
_UOM = {
    "GRAM": "g",
    "GRAMS": "g",
    "KILO": "kg",
    "KILOGRAM": "kg",
    "MILLILITRE": "ml",
    "MILLILITER": "ml",
    "LITRE": "l",
    "LITER": "l",
    "PIECES": "pc",
    "PIECE": "pc",
    "PACK": "pc",
    "UNIT": "pc",
}

_SPONSORED_HINTS = ("ADS", "SPONSORED", "PCA", "PROMOTED")


def _is_sponsored_widget(widget: dict[str, Any]) -> bool:
    label = f"{widget.get('widgetId', '')} {widget.get('widgetName', '')}".upper()
    return any(hint in label for hint in _SPONSORED_HINTS)


def _product_nodes(node: Any, path: str) -> Iterator[tuple[dict[str, Any], str]]:
    """Yield (product node, json-ish path) for every product under `node`.

    A product node is any dict carrying `sellingPrice` and a `product` sub-dict.
    This covers both the grid shape and the ads shape without special-casing.
    """
    if isinstance(node, dict):
        if "sellingPrice" in node and isinstance(node.get("product"), dict):
            yield node, path
            return
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                yield from _product_nodes(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _product_nodes(value, f"{path}[{index}]")


def _shelf_price_paise(node: dict[str, Any]) -> float | None:
    """The price a logged-out shopper sees.

    Deliberately ignores every member/tier price field.
    """
    candidates = [
        node.get("discountedSellingPrice"),
        node.get("sellingPrice"),
    ]
    values = [float(v) for v in candidates if isinstance(v, (int, float)) and v > 0]
    return min(values) if values else None


def _size(variant: dict[str, Any]) -> tuple[float | None, str | None]:
    packsize = variant.get("packsize")
    unit = _UOM.get(str(variant.get("unitOfMeasure") or "").upper())
    if isinstance(packsize, (int, float)) and packsize > 0 and unit:
        return float(packsize), unit

    qty, parsed_unit = parse_packsize(variant.get("formattedPacksize"))
    if qty is not None:
        return qty, parsed_unit

    weight = variant.get("weightInGms")
    if isinstance(weight, (int, float)) and weight > 0:
        return float(weight), "g"
    return None, None


def _image_path(variant: dict[str, Any]) -> str | None:
    images = variant.get("images")
    if isinstance(images, list) and images and isinstance(images[0], dict):
        # Stored verbatim: the CDN host is not in the payload and we do not guess it.
        path = images[0].get("path")
        return path if isinstance(path, str) and path else None
    return None


def map_zepto(payload: Any) -> list[NormalizedRow]:
    # branch 1: collector rows (live)
    collector_rows = [record for record in as_records(payload) if is_collector_row(record)]
    if collector_rows:
        return map_collector_rows(collector_rows, "zepto")

    # branch 2: raw search response (mock fixtures, offline captures)
    record = unwrap_record(payload)
    body = site_payload(record, "layout")

    # Page-level facts live on the wrapper here, so they apply to every row.
    eta_min = record.get("eta_minutes")
    eta_min = int(eta_min) if isinstance(eta_min, (int, float)) else None
    captured_at = record.get("captured_at")
    captured_at = captured_at if isinstance(captured_at, str) else None
    resolved_area = _text(record.get("resolved_area"))

    rows: list[NormalizedRow] = []
    layout = body.get("layout")
    if not isinstance(layout, list):
        return rows

    for w_index, widget in enumerate(layout):
        if not isinstance(widget, dict):
            continue
        sponsored = _is_sponsored_widget(widget)
        # Every level is checked, because `.get("resolver", {})` returns None
        # rather than {} when the key is present and explicitly null — and the
        # AttributeError that followed took the whole universe down over one
        # malformed widget on a page whose other widgets were fine.
        data = widget.get("data")
        resolver = data.get("resolver") if isinstance(data, dict) else None
        resolved = resolver.get("data") if isinstance(resolver, dict) else None
        items = resolved.get("items") if isinstance(resolved, dict) else None
        if not isinstance(items, list):
            continue

        for node, sub_path in _product_nodes(items, f"layout[{w_index}].items"):
            product = node.get("product") or {}
            variant = node.get("productVariant") or {}

            name = str(product.get("name") or "").strip()
            if not name:
                continue

            price_paise = _shelf_price_paise(node)
            mrp_paise = node.get("mrp")
            qty, unit = _size(variant)
            price = round(price_paise / PAISE, 2) if price_paise is not None else None
            mrp = (
                round(float(mrp_paise) / PAISE, 2)
                if isinstance(mrp_paise, (int, float)) and mrp_paise > 0
                else None
            )
            up, basis = unit_price(price, qty, unit)

            available = node.get("availableQuantity")
            available = int(available) if isinstance(available, (int, float)) else None
            in_stock = not bool(node.get("outOfStock")) and (available is None or available > 0)

            rows.append(
                NormalizedRow(
                    universe="zepto",
                    name=name,
                    brand=(str(product.get("brand")).strip() or None)
                    if product.get("brand")
                    else None,
                    variant=variant_label(name),
                    qty=qty,
                    unit=unit,
                    price=price,
                    mrp=mrp,
                    unit_price=up,
                    unit_price_basis=basis,
                    in_stock=in_stock,
                    qty_available=available,
                    eta_min=eta_min,
                    sponsored=sponsored,
                    product_id=str(variant.get("id")) if variant.get("id") else None,
                    image_url=_image_path(variant),
                    raw_ref=sub_path,
                    captured_at=captured_at,
                    resolved_area=resolved_area,
                )
            )

    return rows
