"""Product and search links: the shape of every URL this app builds.

No collector reports a URL, so every link here is constructed from the site's
own product id and a per-site template in `server/product_links.py`. One test
per site pins the shape that template produces, so a pattern that is retuned
after being checked against the live site fails loudly here rather than quietly
sending somebody to a 404.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import make_settings
from server.app import create_app
from server.product_links import (
    PRODUCT_URL_TEMPLATES,
    SEARCH_URL_TEMPLATES,
    product_url,
    search_url,
    slugify,
)
from server.registry import universes
from server.resolve import NormalizedRow

NAME = "Amul Butter (Salted), 500 g"


# -- one per site -------------------------------------------------------------
def test_blinkit_product_url_carries_the_id_after_prid() -> None:
    """Checked against the live site through a browser: this lands on the exact
    product."""
    assert (
        product_url("blinkit", "483920", NAME)
        == "https://blinkit.com/prn/amul-butter-salted-500-g/prid/483920"
    )


def test_chaos_product_url_points_at_the_page_this_app_serves() -> None:
    assert product_url("chaos", "cm-1001", NAME) == "/chaos/product/cm-1001"


def test_zepto_has_no_product_link_because_its_route_wants_a_variant_id() -> None:
    """Deliberately off, not forgotten. Zepto's /pvid/ route wants its VARIANT
    id and the collector does not export one, so the id we hold would build a
    link to some other listing. Plain text is the honest answer."""
    assert PRODUCT_URL_TEMPLATES["zepto"] is None
    assert product_url("zepto", "a1b2c3", NAME) is None


def test_instamart_has_no_product_link_because_its_page_is_store_gated() -> None:
    """Deliberately off, not forgotten. The Instamart product page does not
    resolve for a visitor whose session has not picked the same store."""
    assert PRODUCT_URL_TEMPLATES["instamart"] is None
    assert product_url("instamart", "XJ8K2P", NAME) is None


def test_every_universe_in_the_registry_has_a_decision_recorded() -> None:
    """On or off, each universe is IN the map. A universe missing from it looks
    exactly like one switched off, and the two mean different things: one was
    checked, the other was never considered."""
    assert set(PRODUCT_URL_TEMPLATES) == {"zepto", "blinkit", "instamart", "chaos"}
    assert set(SEARCH_URL_TEMPLATES) == {"zepto", "blinkit", "instamart", "chaos"}


@pytest.mark.parametrize("universe", sorted(PRODUCT_URL_TEMPLATES))
def test_every_product_template_puts_the_id_in_the_url(universe: str) -> None:
    """Whatever the shape, the id is the part that identifies the product. A
    template that dropped it would build a link to the wrong page for every row
    at once."""
    if PRODUCT_URL_TEMPLATES[universe] is None:
        pytest.skip(f"{universe} has no product URL pattern")
    built = product_url(universe, "id-4242", NAME)

    assert built is not None and "id-4242" in built


# -- refusals -----------------------------------------------------------------
def test_a_row_with_no_product_id_gets_no_link() -> None:
    """The id IS the link. A row without one has nothing to point at, and a
    guessed URL would look exactly like a real one."""
    assert product_url("blinkit", None, NAME) is None
    assert product_url("blinkit", "", NAME) is None


def test_a_universe_with_no_template_gets_no_link() -> None:
    assert product_url("some-new-universe", "abc", NAME) is None
    assert search_url("some-new-universe") is None


def test_switching_a_site_off_switches_its_links_off(monkeypatch) -> None:
    """The point of the map: one edit turns a site's links off everywhere when
    its pattern turns out to be wrong, and touches no other site."""
    monkeypatch.setitem(PRODUCT_URL_TEMPLATES, "blinkit", None)

    assert product_url("blinkit", "483920", NAME) is None
    assert product_url("chaos", "cm-1001", NAME) is not None  # unaffected


def test_turning_a_site_back_on_is_one_edit(monkeypatch) -> None:
    """And the reverse: the patterns that are off today are off in one place, so
    re-enabling one when the site changes is a single line."""
    monkeypatch.setitem(
        PRODUCT_URL_TEMPLATES, "instamart", "https://www.swiggy.com/instamart/item/{product_id}"
    )

    assert product_url("instamart", "XJ8K2P", NAME) == "https://www.swiggy.com/instamart/item/XJ8K2P"


def test_a_template_naming_an_unknown_placeholder_costs_a_link_not_the_response(
    monkeypatch,
) -> None:
    """A typo while retuning a pattern should degrade to plain text, not raise
    through the API response that carries every other row."""
    monkeypatch.setitem(PRODUCT_URL_TEMPLATES, "blinkit", "https://x/{sku}/{product_id}")

    assert product_url("blinkit", "a1b2c3", NAME) is None


def test_an_id_with_url_characters_in_it_is_escaped() -> None:
    built = product_url("blinkit", "a/b?c", NAME)

    assert built == "https://blinkit.com/prn/amul-butter-salted-500-g/prid/a%2Fb%3Fc"


# -- the slug is cosmetic -----------------------------------------------------
def test_the_slug_is_lowercase_hyphenated_and_bounded() -> None:
    assert slugify("Amul Butter (Salted), 500 g") == "amul-butter-salted-500-g"
    assert slugify("  ") == "p"  # a path segment has to be something
    assert slugify(None) == "p"
    assert len(slugify("x " * 200)) <= 60
    assert not slugify("x " * 200).endswith("-")


# -- site searches ------------------------------------------------------------
@pytest.mark.parametrize("universe", sorted(SEARCH_URL_TEMPLATES))
def test_every_search_template_carries_the_query(universe: str) -> None:
    """Searches stay on for every live universe, including the two whose product
    pages are unreachable: a search is not a listing, and all three sites answer
    one for any visitor."""
    if SEARCH_URL_TEMPLATES[universe] is None:
        pytest.skip(f"{universe} has no search pattern")
    built = search_url(universe, "amul butter", "560001")

    assert built is not None and "amul%20butter" in built


def test_the_search_links_are_the_ones_the_sites_use() -> None:
    assert search_url("zepto", "amul butter") == "https://www.zeptonow.com/search?query=amul%20butter"
    assert search_url("blinkit", "amul butter") == "https://blinkit.com/s/?q=amul%20butter"
    assert (
        search_url("instamart", "amul butter")
        == "https://www.swiggy.com/instamart/search?custom_back=true&query=amul%20butter"
    )
    assert search_url("chaos", "amul butter", "560001") == "/chaos/search?q=amul%20butter&pincode=560001"


# -- how the app hands them out ----------------------------------------------
def test_a_normalized_row_computes_its_own_product_url() -> None:
    """Computed, never stored: a template edited after a run was captured
    applies to that run too, and a wrong pattern cannot be baked into one."""
    row = NormalizedRow(universe="chaos", name=NAME, product_id="cm-1001")

    assert row.product_url == "/chaos/product/cm-1001"
    assert row.model_dump()["product_url"] == "/chaos/product/cm-1001"
    assert "product_url" not in NormalizedRow.model_fields  # not an input


def test_the_registry_hands_each_universe_its_search_template(tmp_path) -> None:
    rows = {u.id: u for u in universes(make_settings(tmp_path / "runs"))}

    assert rows["zepto"].search_url_template == SEARCH_URL_TEMPLATES["zepto"]
    assert rows["chaos"].search_url_template == SEARCH_URL_TEMPLATES["chaos"]


def test_the_universes_endpoint_publishes_the_search_template(tmp_path) -> None:
    with TestClient(create_app(make_settings(tmp_path / "runs"))) as client:
        rows = {u["id"]: u for u in client.get("/api/universes").json()["universes"]}

    assert rows["blinkit"]["search_url_template"] == SEARCH_URL_TEMPLATES["blinkit"]
    # The template is what travels, not a finished URL: the UI has the run's
    # words and the server, at registry time, does not.
    assert "{query}" in rows["blinkit"]["search_url_template"]
