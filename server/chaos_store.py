"""The chaos store: a small grocery site this app serves itself.

It exists so the reliability story can be demonstrated on demand. The three live
universes point at real sites that redesign on their own schedule; this one
points at a store we host, whose markup we can change on command, so a collector
can be broken and then repaired while somebody is watching.

Two rules make the demonstration mean something:

1. **The versions serve the same data.** `CATALOG` is the single source of
   product facts. `v1` and `v2` are two renderings of it with different tags,
   different class names, different attribute names and different nesting. A
   price that changes between versions would make a repaired collector
   indistinguishable from a broken one.
2. **The data is never in the page as data.** No JSON blob is embedded in the
   HTML. A parser that could read a machine-readable copy of the catalogue would
   survive any redesign, which would make the break, and therefore the repair,
   meaningless. Every field has to come out of the markup.

The URL contract is stable across versions: `/chaos?q=<keyword>&pincode=<6
digits>`. A site redesign changes the DOM, not the address, and the DOM is what
self-healing repairs.

Location behaves the way a real quick-commerce site behaves: with no valid
pincode the store shows a location prompt and no prices at all. The delivering-to
line echoes the pincode that was asked for, which is what the app's location
proof reads (`runs.resolves_pincode`).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Iterable

# Rendering versions, oldest first. `VERSIONS[0]` is the shape a collector is
# first written against.
VERSIONS: tuple[str, ...] = ("v1", "v2")
DEFAULT_VERSION = VERSIONS[0]

PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")

STORE_NAME = "ChaosMart"

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Product:
    """One shelf listing. The field names mirror the 18-field collector-row
    contract in `server/mappers/collector_rows.py`, because that is what a
    collector reading this page has to produce."""

    product_id: str
    name: str
    brand: str
    package_size: str
    selling_price: int
    mrp: int
    in_stock: bool = True
    available_quantity: int | None = None
    sponsored: bool = False
    rating: float | None = None
    image_url: str | None = None
    keywords: tuple[str, ...] = ()

    @property
    def haystack(self) -> str:
        return " ".join((self.name, self.brand, " ".join(self.keywords))).lower()


# Prices are invented and belong to this fictional store. They are deliberately
# in the same band as the real universes so a comparison row looks like a
# comparison row.
CATALOG: tuple[Product, ...] = (
    Product(
        product_id="cm-1001",
        name="Amul Butter Salted",
        brand="Amul",
        package_size="100 g",
        selling_price=61,
        mrp=64,
        available_quantity=18,
        rating=4.6,
        image_url="/chaos/static/butter-100.png",
        keywords=("butter", "makhan", "dairy", "table butter"),
    ),
    Product(
        product_id="cm-1002",
        name="Amul Butter Salted",
        brand="Amul",
        package_size="500 g",
        selling_price=279,
        mrp=295,
        available_quantity=6,
        sponsored=True,
        rating=4.5,
        image_url="/chaos/static/butter-500.png",
        keywords=("butter", "dairy", "family pack"),
    ),
    Product(
        product_id="cm-1003",
        name="Amul Unsalted Butter",
        brand="Amul",
        package_size="100 g",
        selling_price=66,
        mrp=68,
        available_quantity=9,
        rating=4.4,
        image_url="/chaos/static/butter-unsalted.png",
        keywords=("butter", "white butter", "dairy", "cooking"),
    ),
    Product(
        product_id="cm-1004",
        name="Amul Butter Salted",
        brand="Amul",
        package_size="100 g X 2",
        selling_price=118,
        mrp=128,
        available_quantity=4,
        rating=4.3,
        image_url="/chaos/static/butter-multipack.png",
        keywords=("butter", "multipack", "combo", "dairy"),
    ),
    Product(
        product_id="cm-1005",
        name="Amul Butter Salted",
        brand="Amul",
        package_size="50 g",
        selling_price=32,
        mrp=34,
        in_stock=False,
        available_quantity=0,
        rating=4.2,
        image_url="/chaos/static/butter-50.png",
        keywords=("butter", "mini", "dairy"),
    ),
    Product(
        product_id="cm-1006",
        name="Nandini Butter Salted",
        brand="Nandini",
        package_size="100 g",
        selling_price=54,
        mrp=58,
        available_quantity=21,
        rating=4.1,
        image_url="/chaos/static/nandini-butter.png",
        keywords=("butter", "dairy"),
    ),
    Product(
        product_id="cm-1007",
        name="Mother Dairy Butter Salted",
        brand="Mother Dairy",
        package_size="100 g",
        selling_price=57,
        mrp=60,
        available_quantity=12,
        rating=4.0,
        image_url="/chaos/static/mother-dairy-butter.png",
        keywords=("butter", "dairy"),
    ),
    Product(
        product_id="cm-1008",
        name="Amul Cheese Slices",
        brand="Amul",
        package_size="200 g",
        selling_price=131,
        mrp=140,
        available_quantity=15,
        rating=4.4,
        image_url="/chaos/static/cheese-slices.png",
        keywords=("cheese", "dairy", "sandwich"),
    ),
    Product(
        product_id="cm-1009",
        name="Amul Taaza Toned Milk",
        brand="Amul",
        package_size="500 ml",
        selling_price=27,
        mrp=28,
        available_quantity=40,
        rating=4.7,
        image_url="/chaos/static/taaza-milk.png",
        keywords=("milk", "dairy", "toned"),
    ),
    Product(
        product_id="cm-1010",
        name="Britannia Brown Bread",
        brand="Britannia",
        package_size="400 g",
        selling_price=49,
        mrp=52,
        available_quantity=11,
        sponsored=True,
        rating=4.2,
        image_url="/chaos/static/brown-bread.png",
        keywords=("bread", "bakery", "breakfast"),
    ),
    Product(
        product_id="cm-1011",
        name="Aashirvaad Shudh Chakki Atta",
        brand="Aashirvaad",
        package_size="5 kg",
        selling_price=269,
        mrp=299,
        available_quantity=7,
        rating=4.5,
        image_url="/chaos/static/atta-5kg.png",
        keywords=("atta", "flour", "wheat", "staples"),
    ),
    Product(
        product_id="cm-1012",
        name="Tata Salt Iodised",
        brand="Tata",
        package_size="1 kg",
        selling_price=27,
        mrp=30,
        available_quantity=33,
        rating=4.6,
        image_url="/chaos/static/tata-salt.png",
        keywords=("salt", "namak", "staples"),
    ),
    Product(
        product_id="cm-1013",
        name="Fortune Sunlite Refined Sunflower Oil",
        brand="Fortune",
        package_size="1 l",
        selling_price=147,
        mrp=165,
        available_quantity=9,
        rating=4.3,
        image_url="/chaos/static/sunflower-oil.png",
        keywords=("oil", "tel", "cooking", "staples"),
    ),
    Product(
        product_id="cm-1014",
        name="Maggi 2-Minute Masala Noodles",
        brand="Maggi",
        package_size="70 g X 4",
        selling_price=94,
        mrp=98,
        available_quantity=25,
        rating=4.8,
        image_url="/chaos/static/maggi-4pack.png",
        keywords=("noodles", "instant", "snacks", "multipack"),
    ),
    Product(
        product_id="cm-1015",
        name="Parle-G Original Glucose Biscuits",
        brand="Parle",
        package_size="250 g",
        selling_price=29,
        mrp=30,
        available_quantity=52,
        rating=4.5,
        image_url="/chaos/static/parle-g.png",
        keywords=("biscuits", "glucose", "snacks", "tea time"),
    ),
    Product(
        product_id="cm-1016",
        name="Surf Excel Easy Wash Detergent Powder",
        brand="Surf Excel",
        package_size="1 kg",
        selling_price=132,
        mrp=145,
        available_quantity=14,
        rating=4.4,
        image_url="/chaos/static/surf-excel-1kg.png",
        keywords=("detergent", "washing powder", "laundry", "household"),
    ),
    Product(
        product_id="cm-1017",
        name="Colgate Strong Teeth Toothpaste",
        brand="Colgate",
        package_size="200 g",
        selling_price=118,
        mrp=128,
        available_quantity=19,
        rating=4.5,
        image_url="/chaos/static/colgate-200.png",
        keywords=("toothpaste", "oral care", "dental", "household"),
    ),
    Product(
        product_id="cm-1018",
        name="Tata Sampann Unpolished Toor Dal",
        brand="Tata",
        package_size="1 kg",
        selling_price=179,
        mrp=199,
        available_quantity=8,
        rating=4.3,
        image_url="/chaos/static/toor-dal-1kg.png",
        keywords=("dal", "toor", "arhar", "pulses", "staples"),
    ),
    Product(
        product_id="cm-1019",
        name="Red Label Natural Care Tea",
        brand="Brooke Bond",
        package_size="500 g",
        selling_price=285,
        mrp=310,
        available_quantity=5,
        sponsored=True,
        rating=4.2,
        image_url="/chaos/static/red-label-500.png",
        keywords=("tea", "chai", "beverages", "leaf tea"),
    ),
    Product(
        product_id="cm-1020",
        name="Sunfeast Dark Fantasy Choco Fills",
        brand="Sunfeast",
        package_size="75 g X 3",
        selling_price=105,
        mrp=114,
        in_stock=False,
        available_quantity=0,
        rating=4.6,
        image_url="/chaos/static/choco-fills-3pack.png",
        keywords=("biscuits", "cookies", "chocolate", "snacks", "multipack"),
    ),
    Product(
        product_id="cm-1021",
        name="Vim Dishwash Bar",
        brand="Vim",
        package_size="300 g",
        selling_price=39,
        mrp=42,
        available_quantity=27,
        rating=4.4,
        image_url="/chaos/static/vim-bar-300.png",
        keywords=("dishwash", "bar", "utensils", "cleaning", "household"),
    ),
    Product(
        product_id="cm-1022",
        name="Saffola Gold Blended Cooking Oil",
        brand="Saffola",
        package_size="1 l",
        selling_price=189,
        mrp=210,
        available_quantity=10,
        rating=4.1,
        image_url="/chaos/static/saffola-gold-1l.png",
        keywords=("oil", "tel", "cooking", "staples"),
    ),
)


def query_tokens(query: str | None) -> list[str]:
    return [token for token in _TOKEN_SPLIT.split((query or "").lower()) if token]


def matches(product: Product, query: str | None) -> bool:
    """Whether one product answers the query. An empty query matches everything,
    which is what the store front page is."""
    tokens = query_tokens(query)
    return all(token in product.haystack for token in tokens)


def matching(query: str | None) -> list[Product]:
    """Only the products whose name, brand or keywords contain all query tokens."""
    return [product for product in CATALOG if matches(product, query)]


def search(query: str | None) -> list[Product]:
    """The whole shelf, every time, with the products that answer `query` first.

    A store that hides everything it was not asked about turns a visit into a
    guessing game: type a word the catalogue was not built around and the shop
    looks empty. The query still does something - it orders the shelf and marks
    the hits, and every rendering carries `data-match` per product - but nothing
    is withheld, so any search shows a stocked store.
    """
    hits = matching(query)
    if len(hits) == len(CATALOG):
        return list(CATALOG)
    hit_ids = {product.product_id for product in hits}
    return hits + [product for product in CATALOG if product.product_id not in hit_ids]


def find_product(product_id: str | None) -> Product | None:
    """One shelf listing by id, for the product page."""
    for product in CATALOG:
        if product.product_id == product_id:
            return product
    return None


def store_id_for(pincode: str) -> str:
    """The hub id this store reports for a pincode. Stable per pincode so a run
    can be checked against an earlier one."""
    return f"CM-{pincode}-01"


def resolved_area_for(pincode: str) -> str:
    """The store's own delivering-to line.

    The requested pincode appears on its own digit boundary, which is exactly
    what `runs.resolves_pincode` looks for. No place name is used: this store is
    fictional and has no neighbourhoods.
    """
    return f"Delivering to {pincode} - {STORE_NAME} hub {store_id_for(pincode)}"


def eta_minutes_for(pincode: str) -> int:
    """A listed delivery time, derived from the pincode so it is stable."""
    return 8 + (sum(int(digit) for digit in pincode) % 9)


class ChaosStore:
    """Server-side state: which rendering the store is currently serving.

    One instance per app. The version is read from the environment at startup and
    changed at runtime only through the token-protected flip endpoint, so a
    version change is always a deliberate act by whoever holds the token.
    """

    def __init__(self, version: str = DEFAULT_VERSION) -> None:
        self._version = version if version in VERSIONS else DEFAULT_VERSION

    @property
    def version(self) -> str:
        return self._version

    def flip(self, version: str | None = None) -> str:
        """Move to `version`, or to the next one in `VERSIONS` when none is given.

        Returns the version now being served. An unknown version is refused
        rather than silently ignored.
        """
        if version is None:
            index = VERSIONS.index(self._version)
            self._version = VERSIONS[(index + 1) % len(VERSIONS)]
            return self._version
        if version not in VERSIONS:
            raise ValueError(f"unknown store version {version!r} - known: {', '.join(VERSIONS)}")
        self._version = version
        return self._version

    def state(self) -> dict[str, Any]:
        return {"version": self._version, "versions": list(VERSIONS), "products": len(CATALOG)}

    def render(self, query: str | None, pincode: str | None) -> str:
        return render_page(self._version, query, pincode)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def _rupees(value: int) -> str:
    return f"₹{value}"


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _match_flag(product: Product, query: str) -> str:
    return "true" if matches(product, query) else "false"


def product_path(product_id: str, pincode: str = "") -> str:
    """Where one product's own page lives. The pincode rides along so the page
    can show prices for the same location the shelf was priced for; a single
    parameter, so no ampersand has to be escaped into the href."""
    base = f"/chaos/product/{product_id}"
    return f"{base}?pincode={pincode}" if pincode else base


def _no_match_note(query: str, products: list[Product]) -> str:
    """The store's answer when the search matched nothing on the shelf.

    Shown ABOVE the full catalogue rather than instead of it: the shelf is real
    whatever was typed, and hiding it would make the store look shut.
    """
    if not query or any(matches(product, query) for product in products):
        return ""
    return (
        '<p class="empty-note">No products matched that search. '
        "The full shelf is below.</p>"
    )


_STYLE_V1 = """
body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#12131a}
.store-head{background:#155dfc;color:#fff;padding:14px 18px}
.brand-mark{font-size:20px;font-weight:700;margin:0}
.delivery-area{margin:6px 0 0;font-size:13px}
.finder{padding:14px 18px;display:flex;gap:8px;flex-wrap:wrap}
.finder input{padding:8px 10px;border:1px solid #c8cbd4;border-radius:6px}
.results{display:flex;flex-wrap:wrap;gap:12px;padding:0 18px 24px}
.product-card{background:#fff;border:1px solid #e2e4ea;border-radius:10px;padding:12px;width:210px}
.product-title{font-size:15px;margin:0 0 4px}
.product-brand,.pack-size,.rating-value{font-size:12px;color:#5a5f6e;margin:0}
.price-now{font-size:17px;font-weight:700}
.price-mrp{font-size:13px;color:#8a8f9c;text-decoration:line-through;margin-left:6px}
.sponsored-tag{font-size:11px;color:#a15c00;margin:4px 0 0}
.stock-state{font-size:12px;margin:4px 0 0}
.empty-note,.location-prompt{padding:18px;font-size:14px}
.product-card[data-match="true"]{border-color:#155dfc;box-shadow:0 0 0 2px #dbeafe}
.product-link{display:inline-block;margin-top:8px;font-size:12px;color:#155dfc}
"""

_STYLE_V2 = """
body{font-family:Georgia,serif;margin:0;background:#fffdf7;color:#22201c}
.catalog-bar{background:#22201c;color:#fdf6e3;padding:12px 16px;display:flex;
  justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
.brandmark{font-size:19px;letter-spacing:1px}
.service-area{font-size:12px}
.finder-strip{padding:12px 16px}
.finder-strip input{padding:7px 9px;border:1px solid #b9b1a0;background:#fff}
.listing-table{width:100%;border-collapse:collapse;font-size:14px}
.listing-table th{text-align:left;padding:8px 10px;border-bottom:2px solid #22201c;font-size:12px}
.listing-row td{padding:10px;border-bottom:1px solid #e6dfd0;vertical-align:top}
.item-label{display:block;font-weight:700}
.item-maker,.item-measure{display:block;font-size:12px;color:#6d6455}
.cost-value{font-weight:700}
.cost-list{font-size:12px;color:#8d8474;text-decoration:line-through;margin-left:6px}
.flag-promoted{display:block;font-size:11px;color:#8a4b00}
.flag-availability{display:block;font-size:12px}
.thumb{width:46px;height:46px;object-fit:cover}
.empty-note,.location-prompt{padding:16px;font-size:14px}
.listing-row[data-match="true"] td{background:#fdf3d7}
.item-link{font-size:12px;color:#8a4b00}
"""

_STYLE_PRODUCT = """
body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#12131a}
.detail-head{background:#155dfc;color:#fff;padding:14px 18px}
.detail-head a{color:#fff}
.detail{max-width:620px;margin:18px auto;background:#fff;border:1px solid #e2e4ea;
  border-radius:10px;padding:18px;display:flex;gap:18px;flex-wrap:wrap}
.detail img{width:96px;height:96px;object-fit:cover;border-radius:8px}
.detail dl{margin:0;display:grid;grid-template-columns:auto auto;gap:4px 14px;font-size:14px}
.detail dt{color:#5a5f6e}
.detail dd{margin:0}
.detail-name{margin:0 0 6px;font-size:18px}
.detail-note{max-width:620px;margin:0 auto;padding:0 18px 18px;font-size:12px;color:#5a5f6e}
.location-prompt{padding:18px;font-size:14px}
"""


def _page(title: str, style: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title><style>{style}</style></head>"
        f"<body>{body}</body></html>"
    )


def _finder_v1(query: str, pincode: str) -> str:
    return (
        '<form class="finder" action="/chaos" method="get">'
        f'<input id="search-input" name="q" value="{_esc(query)}" placeholder="Search products">'
        f'<input id="pincode-input" name="pincode" value="{_esc(pincode)}" '
        'placeholder="6-digit pincode" maxlength="6">'
        '<button id="apply-location" type="submit">Show prices</button>'
        "</form>"
    )


def _finder_v2(query: str, pincode: str) -> str:
    return (
        '<form class="finder-strip" action="/chaos" method="get">'
        f'<input id="keywordField" name="q" value="{_esc(query)}" placeholder="Search products">'
        f'<input id="areaField" name="pincode" value="{_esc(pincode)}" '
        'placeholder="6-digit pincode" maxlength="6">'
        '<button id="loadCatalog" type="submit">Show prices</button>'
        "</form>"
    )


def _card_v1(product: Product, query: str = "", pincode: str = "") -> str:
    parts = [
        '<article class="product-card" '
        f'data-match="{_match_flag(product, query)}" '
        f'data-product-id="{_esc(product.product_id)}">',
        f'<img class="product-image" src="{_esc(product.image_url or "")}" alt="">',
        f'<h3 class="product-title">{_esc(product.name)}</h3>',
        f'<p class="product-brand">{_esc(product.brand)}</p>',
        f'<p class="pack-size">{_esc(product.package_size)}</p>',
        '<p class="price-block">'
        f'<span class="price-now">{_rupees(product.selling_price)}</span>'
        f'<span class="price-mrp">{_rupees(product.mrp)}</span></p>',
    ]
    if product.sponsored:
        parts.append('<p class="sponsored-tag">Sponsored</p>')
    stock_text = "In stock" if product.in_stock else "Out of stock"
    quantity = "" if product.available_quantity is None else str(product.available_quantity)
    parts.append(
        f'<p class="stock-state" data-in-stock="{"true" if product.in_stock else "false"}" '
        f'data-available="{_esc(quantity)}">{stock_text}</p>'
    )
    if product.rating is not None:
        parts.append(f'<p class="rating-value">{product.rating}</p>')
    parts.append(
        f'<a class="product-link" href="{_esc(product_path(product.product_id, pincode))}">'
        "View product</a>"
    )
    parts.append("</article>")
    return "".join(parts)


def _row_v2(product: Product, query: str = "", pincode: str = "") -> str:
    promoted = '<span class="flag-promoted">Promoted</span>' if product.sponsored else ""
    stock_text = "Available now" if product.in_stock else "Sold out"
    quantity = "" if product.available_quantity is None else str(product.available_quantity)
    rating = "" if product.rating is None else str(product.rating)
    return (
        f'<tr class="listing-row" data-match="{_match_flag(product, query)}" '
        f'data-sku="{_esc(product.product_id)}" '
        f'data-stock="{"in" if product.in_stock else "out"}" data-units="{_esc(quantity)}">'
        f'<td class="cell-image"><img class="thumb" src="{_esc(product.image_url or "")}" alt=""></td>'
        '<td class="cell-identity">'
        f'<span class="item-label">{_esc(product.name)}</span>'
        f'<span class="item-maker">{_esc(product.brand)}</span>'
        f'<span class="item-measure">{_esc(product.package_size)}</span></td>'
        '<td class="cell-cost">'
        f'<span class="cost-value" data-rupees="{product.selling_price}">'
        f"{_rupees(product.selling_price)}</span>"
        f'<span class="cost-list" data-rupees="{product.mrp}">{_rupees(product.mrp)}</span></td>'
        f'<td class="cell-flags">{promoted}'
        f'<span class="flag-availability">{stock_text}</span></td>'
        f'<td class="cell-score">{_esc(rating)}</td>'
        '<td class="cell-link">'
        f'<a class="item-link" href="{_esc(product_path(product.product_id, pincode))}">'
        "View product</a></td>"
        "</tr>"
    )


def _location_prompt_v1() -> str:
    return (
        '<section class="location-prompt" id="location-prompt">'
        "Enter a 6-digit pincode to see prices for your area.</section>"
    )


def _location_prompt_v2() -> str:
    return (
        '<section class="location-prompt" id="areaPrompt">'
        "Enter a 6-digit pincode to see prices for your area.</section>"
    )


def _render_v1(products: list[Product], query: str, pincode: str) -> str:
    if not pincode:
        body = (
            '<main class="store" data-store-version="v1">'
            f'<header class="store-head"><p class="brand-mark">{STORE_NAME}</p></header>'
            f"{_finder_v1(query, '')}{_location_prompt_v1()}</main>"
        )
        return _page(f"{STORE_NAME}", _STYLE_V1, body)

    cards = "".join(_card_v1(product, query, pincode) for product in products)
    if not cards:
        cards = '<p class="empty-note">No products matched that search.</p>'
    body = (
        '<main class="store" data-store-version="v1">'
        '<header class="store-head">'
        f'<p class="brand-mark">{STORE_NAME}</p>'
        f'<p class="delivery-area" id="delivery-area" data-hub="{_esc(store_id_for(pincode))}">'
        f"{_esc(resolved_area_for(pincode))}</p>"
        f'<p class="eta-line">Delivery in <span class="eta-value">'
        f"{eta_minutes_for(pincode)} minutes</span></p>"
        "</header>"
        f"{_finder_v1(query, pincode)}"
        f"{_no_match_note(query, products)}"
        f'<section class="results" data-result-count="{len(products)}">{cards}</section>'
        "</main>"
    )
    return _page(f"{STORE_NAME} - {query or 'all products'}", _STYLE_V1, body)


def _render_v2(products: list[Product], query: str, pincode: str) -> str:
    if not pincode:
        body = (
            '<div class="catalog" data-layout="v2">'
            f'<div class="catalog-bar"><span class="brandmark">{STORE_NAME}</span></div>'
            f"{_finder_v2(query, '')}{_location_prompt_v2()}</div>"
        )
        return _page(f"{STORE_NAME}", _STYLE_V2, body)

    rows = "".join(_row_v2(product, query, pincode) for product in products)
    table = (
        '<table class="listing-table" data-rows="{count}"><thead><tr>'
        '<th scope="col">Item</th><th scope="col">Product</th><th scope="col">Cost</th>'
        '<th scope="col">Status</th><th scope="col">Score</th><th scope="col">Page</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>"
    ).format(count=len(products))
    if not rows:
        table = '<p class="empty-note">No products matched that search.</p>'
    body = (
        '<div class="catalog" data-layout="v2">'
        '<div class="catalog-bar">'
        f'<span class="brandmark">{STORE_NAME}</span>'
        f'<span class="service-area" id="serviceArea" data-node="{_esc(store_id_for(pincode))}">'
        f"{_esc(resolved_area_for(pincode))}</span>"
        f'<span class="drop-eta">{eta_minutes_for(pincode)} min drop</span>'
        "</div>"
        f"{_finder_v2(query, pincode)}{_no_match_note(query, products)}{table}"
        "</div>"
    )
    return _page(f"{STORE_NAME} - {query or 'all products'}", _STYLE_V2, body)


_RENDERERS = {"v1": _render_v1, "v2": _render_v2}


def clean_pincode(raw: str | None) -> str:
    """The pincode the store will serve, or an empty string.

    Anything that is not a 6-digit Indian pincode leaves the store with no
    location, which is the state that shows the prompt and no prices.
    """
    value = (raw or "").strip()
    return value if PINCODE_RE.match(value) else ""


def render_page(version: str, query: str | None, pincode: str | None) -> str:
    """The store page for one version, query and pincode."""
    renderer = _RENDERERS.get(version, _render_v1)
    clean_query = " ".join((query or "").split())[:60]
    return renderer(search(clean_query), clean_query, clean_pincode(pincode))


# ---------------------------------------------------------------------------
# the product page
# ---------------------------------------------------------------------------
# One template for both store versions. The redesign the chaos demo turns on is
# the SEARCH page's, because the search page is the one the collector reads; a
# product page that also changed shape would add nothing to the demonstration
# and one more thing to keep in sync.
def _detail_row(term: str, value: str) -> str:
    return f"<dt>{_esc(term)}</dt><dd>{_esc(value)}</dd>"


def render_product_page(product_id: str, pincode: str | None) -> str | None:
    """One product's own page, or None when that id is not on the shelf.

    Location behaves the way the shelf does: with no valid pincode there is a
    prompt and no price, because a price with no delivery area behind it is not
    a price anybody can act on.
    """
    product = find_product(product_id)
    if product is None:
        return None

    resolved = clean_pincode(pincode)
    back = f"/chaos/search?pincode={resolved}" if resolved else "/chaos/search"
    head = (
        '<header class="detail-head">'
        f'<p class="brand-mark">{STORE_NAME}</p>'
        f'<a class="back-link" href="{_esc(back)}">Back to the shelf</a>'
        "</header>"
    )

    if not resolved:
        body = (
            f'<main class="detail-page" data-page="product" '
            f'data-product-id="{_esc(product.product_id)}">{head}'
            '<section class="location-prompt" id="product-location-prompt">'
            "Enter a 6-digit pincode on the shelf to see prices for your area."
            "</section></main>"
        )
        return _page(f"{product.name} - {STORE_NAME}", _STYLE_PRODUCT, body)

    facts = [
        _detail_row("Brand", product.brand),
        _detail_row("Pack", product.package_size),
        _detail_row("Price", _rupees(product.selling_price)),
        _detail_row("MRP", _rupees(product.mrp)),
        _detail_row("Availability", "In stock" if product.in_stock else "Out of stock"),
    ]
    if product.available_quantity is not None:
        facts.append(_detail_row("Units listed", str(product.available_quantity)))
    if product.rating is not None:
        facts.append(_detail_row("Rating", str(product.rating)))
    if product.sponsored:
        facts.append(_detail_row("Placement", "Sponsored"))
    facts.append(_detail_row("Delivering to", resolved_area_for(resolved)))
    facts.append(_detail_row("Listed delivery", f"{eta_minutes_for(resolved)} minutes"))

    body = (
        f'<main class="detail-page" data-page="product" '
        f'data-product-id="{_esc(product.product_id)}">{head}'
        '<section class="detail">'
        f'<img class="detail-image" src="{_esc(product.image_url or "")}" alt="">'
        "<div>"
        f'<h1 class="detail-name">{_esc(product.name)}</h1>'
        f"<dl>{''.join(facts)}</dl>"
        "</div></section>"
        '<p class="detail-note">'
        f"{STORE_NAME} is a demo store this app serves itself. The products, the "
        "prices and the delivery times on this page are invented."
        "</p></main>"
    )
    return _page(f"{product.name} - {STORE_NAME}", _STYLE_PRODUCT, body)


# ---------------------------------------------------------------------------
# product thumbnails
# ---------------------------------------------------------------------------
# Flat colour tiles, one per product, drawn at request time. The store is
# fictional and has no photography; the tiles exist so a shelf looks like a
# shelf and so two listings can be told apart at a glance.
_TILE_COLORS: tuple[tuple[int, int, int], ...] = (
    (21, 93, 252),
    (13, 148, 136),
    (161, 92, 0),
    (124, 58, 237),
    (190, 24, 93),
    (22, 101, 52),
    (194, 65, 12),
    (30, 64, 175),
    (101, 116, 205),
    (133, 77, 14),
    (15, 118, 110),
)

_INITIAL_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _initials(name: str) -> str:
    words = [word for word in _INITIAL_SPLIT.split(name) if word]
    return "".join(word[0] for word in words[:2]).upper() or "CM"


def tile_for_image(name: str) -> tuple[tuple[int, int, int], str]:
    """The colour and the letters for one thumbnail file name.

    Keyed off the product the image belongs to, so a tile is stable for as long
    as that product is on the shelf. An unknown name still gets a tile rather
    than a 404: the file name is the only thing the store promised.
    """
    for product in CATALOG:
        if product.image_url and product.image_url.rsplit("/", 1)[-1] == name:
            index = sum(ord(char) for char in product.product_id) % len(_TILE_COLORS)
            return _TILE_COLORS[index], _initials(product.name)
    index = sum(ord(char) for char in name) % len(_TILE_COLORS)
    return _TILE_COLORS[index], "CM"


def catalog_rows(query: str | None, pincode: str | None) -> list[dict[str, Any]]:
    """The catalogue as flattened rows, for tests and for the mock fixture path.

    This is the shape a collector reading the page has to end up with. It is NOT
    served to anybody: the store pages carry no machine-readable copy of the
    catalogue, because a collector that could read one would never break.
    """
    resolved = clean_pincode(pincode)
    if not resolved:
        return []
    return [
        {
            "product_name": product.name,
            "brand": product.brand,
            "package_size": product.package_size,
            "product_id": product.product_id,
            "mrp": product.mrp,
            "selling_price": product.selling_price,
            "discounted_selling_price": None,
            "out_of_stock": not product.in_stock,
            "available_quantity": product.available_quantity,
            "is_sponsored": product.sponsored,
            "rating": product.rating,
            "image_url": product.image_url,
            "serp_screenshot": None,
            "store_id": store_id_for(resolved),
            "requested_pincode": resolved,
            "resolved_area": resolved_area_for(resolved),
            "eta_minutes": eta_minutes_for(resolved),
            "captured_at": None,
        }
        for product in search(query)
    ]


def product_ids(products: Iterable[Product]) -> list[str]:
    return [product.product_id for product in products]
