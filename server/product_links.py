"""Where a row's own listing lives on the site it came from.

The 18-field collector contract carries no product URL: none of the three live
sites puts one in the payload we asked for. What every row does carry is the
site's own product id, and each site routes its listing pages on that id, so the
link can be built from a per-site template.

THIS FILE IS THE ONLY PLACE A URL PATTERN IS WRITTEN DOWN. To change one, edit
the template. To switch a site off, set its template to `None`: the row then
renders as plain text instead of as a link, which is the right answer for a
pattern that has not been verified. A link that 404s is worse than no link,
because it looks like the app got the product wrong.

Nothing here is stored. `NormalizedRow.product_url` is computed from the row
every time it is serialised, so editing a template takes effect on runs captured
before the edit, and a pattern that turns out to be wrong can never be baked
into a saved run.
"""

from __future__ import annotations

import re
from urllib.parse import quote

# -- product pages ----------------------------------------------------------
# `{product_id}` is the id the collector read off the site. `{slug}` is the
# product name, slugified; it is COSMETIC on the site that takes one, which
# routes on the id, and it is there only because its own links carry it and a
# pasted link without one looks wrong.
#
# EDIT HERE. `None` = no product link for that universe, and its rows render as
# plain text. Each pattern below was checked against the live site through a
# browser; the two that are off are off because the check FAILED, and the
# pattern that failed is kept next to the reason so re-enabling one is a copy,
# not a rediscovery.
PRODUCT_URL_TEMPLATES: dict[str, str | None] = {
    # VERIFIED live: lands on the exact product.
    "blinkit": "https://blinkit.com/prn/{slug}/prid/{product_id}",
    # Served by this app, so this one is certain rather than inferred.
    "chaos": "/chaos/product/{product_id}",
    # OFF. The /pvid/ route wants Zepto's VARIANT id, and the collector does not
    # export one, so the id we hold would build a link to some other listing.
    # Re-enable with: "https://www.zeptonow.com/pn/{slug}/pvid/{product_id}"
    "zepto": None,
    # OFF. The Instamart product page is store-gated, so a bare item link does
    # not resolve for a visitor whose session has not picked the same store.
    # Re-enable with: "https://www.swiggy.com/instamart/item/{product_id}"
    "instamart": None,
}

# -- site searches ----------------------------------------------------------
# One per universe, shown once in that universe's column header. NOT per row: a
# search link next to a listing reads as a link TO that listing, and it is not
# one. `{query}` and `{pincode}` are substituted by whoever renders it; a
# template may use either or neither.
#
# These stay on for every live universe even where the product link is off: a
# search is not a listing, and all three sites answer one for any visitor.
#
# EDIT HERE. `None` = no search link for that universe.
SEARCH_URL_TEMPLATES: dict[str, str | None] = {
    "zepto": "https://www.zeptonow.com/search?query={query}",
    "blinkit": "https://blinkit.com/s/?q={query}",
    "instamart": "https://www.swiggy.com/instamart/search?custom_back=true&query={query}",
    "chaos": "/chaos/search?q={query}&pincode={pincode}",
}

_SLUG_NOISE = re.compile(r"[^a-z0-9]+")
SLUG_MAX_LEN = 60


def slugify(value: str | None, max_length: int = SLUG_MAX_LEN) -> str:
    """A product name as a URL path segment.

    Cosmetic, so it is deliberately blunt: lowercase, everything that is not a
    letter or a digit becomes one hyphen, and the result is capped so a long
    listing title cannot push a URL past what a browser will show. An empty
    result falls back to `p`, because a path segment has to be something.
    """
    slug = _SLUG_NOISE.sub("-", (value or "").lower()).strip("-")
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "p"


def product_url(universe: str, product_id: str | None, name: str | None = None) -> str | None:
    """The link to one row's listing, or None when there is no honest one.

    None whenever the universe has no template, the template is switched off, or
    the row carries no product id. The id IS the link, so a row without one has
    nothing to point at.

    A template that names a placeholder this function does not supply yields
    None rather than raising: a typo while retuning a pattern should cost a link,
    not the whole API response.
    """
    template = PRODUCT_URL_TEMPLATES.get(universe)
    if not template or not product_id:
        return None
    try:
        return template.format(
            product_id=quote(str(product_id), safe=""),
            slug=slugify(name),
        )
    except (KeyError, IndexError):
        return None


def search_url(universe: str, query: str = "", pincode: str = "") -> str | None:
    """The link to the site's own search for the same words, or None.

    Belongs in a universe's header, never on a row. See the note above
    `SEARCH_URL_TEMPLATES`.
    """
    template = SEARCH_URL_TEMPLATES.get(universe)
    if not template:
        return None
    try:
        return template.format(
            query=quote(query or "", safe=""),
            pincode=quote(pincode or "", safe=""),
        )
    except (KeyError, IndexError):
        return None
