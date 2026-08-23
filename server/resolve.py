"""Normalized row + entity resolution — DESIGN.md §6.

Completely deterministic and deliberately conservative. A group is
(brand token, base quantity, base unit, variant) PLUS agreement between the
product names themselves, and anything that does not clear both bars is
reported as unmatched rather than guessed at. Refusal beats a wrong match.

Why names are checked at all: brand + pack alone put four different products in
one row. `amul|100|g` collected garlic-and-herbs butter, cheese slices,
margarine and butter chiplets — same brand, same pack size, nothing else in
common — and the table labelled that bucket a confident match. Name agreement is
what stops a bucket from claiming products are the same thing.

Nor does anything here claim certainty. The strongest label the resolver can
honestly emit is `close` — same brand, same pack, names agree — because that is
literally all it checked. NO LLM is involved anywhere in this path, by design.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from .product_links import product_url as build_product_url

# `close` = same brand token, same base pack size, same variant, and the product
# names agree. It is a heuristic and says so. There is deliberately no stronger
# label: this resolver has no product identity to be certain about.
Confidence = Literal["close", "unmatched"]

# Universes whose rows are demonstration data, not shop data. `chaos` points at
# the store this app serves itself, so its prices are invented; putting an
# invented price in the same row as three real ones would make the comparison
# say something that is not true, however honestly the row were labelled. Its
# rows still stream, still validate and are still shown - on their own, under
# their own heading.
DEMO_UNIVERSES: frozenset[str] = frozenset({"chaos"})

# Canonical units. Everything is normalised to a base unit so 1 kg and 1000 g
# group together.
_UNIT_ALIASES: dict[str, str] = {
    "g": "g", "gram": "g", "grams": "g", "gm": "g", "gms": "g", "GRAM": "g",
    "kg": "kg", "kilo": "kg", "kilogram": "kg", "kilograms": "kg", "KILO": "kg",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "MILLILITRE": "ml",
    "l": "l", "ltr": "l", "litre": "l", "liter": "l", "LITRE": "l",
    "pc": "pc", "pcs": "pc", "piece": "pc", "pieces": "pc", "PIECES": "pc",
    "unit": "pc", "units": "pc", "PACK": "pc", "pack": "pc",
}

_TO_BASE: dict[str, tuple[float, str]] = {
    "g": (1.0, "g"),
    "kg": (1000.0, "g"),
    "ml": (1.0, "ml"),
    "l": (1000.0, "ml"),
    "pc": (1.0, "pc"),
}

# Unit-price basis per base unit: (multiplier, label).
_BASIS: dict[str, tuple[float, str]] = {
    "g": (100.0, "per 100 g"),
    "ml": (100.0, "per 100 ml"),
    "pc": (1.0, "per piece"),
}


class NormalizedRow(BaseModel):
    universe: str
    name: str
    brand: str | None = None
    variant: str | None = None  # "salted" / "unsalted" / None
    qty: float | None = None
    unit: str | None = None
    price: float | None = None
    mrp: float | None = None
    unit_price: float | None = None
    unit_price_basis: str | None = None
    in_stock: bool = True
    qty_available: int | None = None
    eta_min: int | None = None
    sponsored: bool = False
    product_id: str | None = None
    image_url: str | None = None
    raw_ref: str | None = None
    captured_at: str | None = None
    # What the site itself said it resolved our pincode to. Provenance, never
    # identity: it is reported next to our own configured area label, never in
    # place of it, and it takes no part in `group_key`.
    resolved_area: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def product_url(self) -> str | None:
        """The row's own listing on the site it came from, built from its
        product id (`server/product_links.py`).

        Computed rather than stored, and derived rather than collected: no
        collector reports a URL, and a pattern that later turns out to be wrong
        must not survive in a saved run. None means there is no honest link,
        which the UI renders as plain text.
        """
        return build_product_url(self.universe, self.product_id, self.name)


class ComparisonGroup(BaseModel):
    key: str
    brand: str | None = None
    qty: float | None = None
    unit: str | None = None
    variant: str | None = None
    confidence: Confidence = "unmatched"
    universes: list[str] = Field(default_factory=list)
    rows: list[NormalizedRow] = Field(default_factory=list)


class Comparison(BaseModel):
    """What `GET /api/runs/{id}` hands the UI.

    `groups` are cross-universe matches (the actual comparison). `unmatched`
    holds single-source groups — real rows we refuse to claim a match for.
    `demo_rows` holds the rows from `DEMO_UNIVERSES`, which take no part in
    either: they are shown, never compared.

    `row_count` and `universe_count` describe the comparison, so they count the
    real universes only. A demo row that inflated the run's row total would make
    the run look like it read more shops than it did.
    """

    groups: list[ComparisonGroup] = Field(default_factory=list)
    unmatched: list[ComparisonGroup] = Field(default_factory=list)
    row_count: int = 0
    universe_count: int = 0
    demo_rows: list[NormalizedRow] = Field(default_factory=list)


def normalize_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    raw = unit.strip()
    return _UNIT_ALIASES.get(raw) or _UNIT_ALIASES.get(raw.lower())


def to_base_qty(qty: float | None, unit: str | None) -> tuple[float | None, str | None]:
    """(500, 'g') -> (500.0, 'g');  (1, 'KILO') -> (1000.0, 'g')."""
    canonical = normalize_unit(unit)
    if qty is None or canonical is None or canonical not in _TO_BASE:
        return None, None
    factor, base = _TO_BASE[canonical]
    return round(qty * factor, 4), base


def unit_price(price: float | None, qty: float | None, unit: str | None) -> tuple[float | None, str | None]:
    """Deterministic per-100g / per-100ml / per-piece price."""
    base_qty, base_unit = to_base_qty(qty, unit)
    if price is None or not base_qty or base_unit is None:
        return None, None
    multiplier, label = _BASIS[base_unit]
    return round(price / base_qty * multiplier, 2), label


_BRAND_NOISE = re.compile(r"[^a-z0-9]+")


def brand_token(brand: str | None, name: str | None = None) -> str:
    """First meaningful token of the brand, lowercased. Falls back to the first
    token of the product name when the payload carries no brand."""
    source = (brand or "").strip() or (name or "").strip()
    if not source:
        return ""
    first = source.split()[0]
    return _BRAND_NOISE.sub("", first.lower())


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Every spelling of a unit we recognise, plus the base units themselves. A token
# that is only a quantity says nothing about WHAT the product is, and the pack
# size is already part of the group key, so counting it twice would inflate the
# agreement between two unrelated products of the same size.
_UNIT_WORDS = frozenset(_UNIT_ALIASES) | frozenset(_UNIT_ALIASES.values())
_UNIT_WORDS = frozenset(word.lower() for word in _UNIT_WORDS)

_QTY_TOKEN = re.compile(
    r"^\d+(?:\.\d+)?(?:" + "|".join(sorted(_UNIT_WORDS, key=len, reverse=True)) + r")?$"
)

# Packaging and grammar, not product identity. "Amul Butter (Pack of 2)" and
# "Amul Butter" are the same thing said twice.
_NAME_STOPWORDS = frozenset(
    {
        "pack", "packs", "packet", "packets", "combo", "of", "and", "with", "the",
        "for", "in", "x", "each", "free", "offer", "value", "saver", "buy", "get",
        "new", "combi",
    }
)

# How much of the two names has to be the same word for the resolver to call two
# rows the same product. Half is a low bar on purpose — it is meant to catch
# word-order and adjective differences ("Amul Pasteurised Butter" vs "Amul
# Butter Pasteurised"), not to be clever.
NAME_AGREEMENT_MIN = 0.5


def name_tokens(row: NormalizedRow) -> frozenset[str]:
    """The words in a product name that actually say what the product IS.

    Brand words, pack sizes, units and packaging filler are all removed: every
    one of those is either already in the group key or carries no identity, so
    leaving them in would make two different products look more alike the more
    they share a shelf.
    """
    brand_words = {
        token
        for token in _TOKEN_SPLIT.split((row.brand or "").lower())
        if token
    }
    out: set[str] = set()
    for token in _TOKEN_SPLIT.split((row.name or "").lower()):
        if not token or token in brand_words or token in _NAME_STOPWORDS:
            continue
        if token in _UNIT_WORDS or _QTY_TOKEN.match(token):
            continue
        out.add(token)
    return frozenset(out)


def name_agreement(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap of two token sets.

    Two names that both reduce to nothing (a listing that is only brand + pack)
    agree trivially — there is no evidence either way, and the brand/pack/variant
    key already had to match to get here. One empty against one populated does
    NOT agree: the populated name says something the other does not.
    """
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def group_key(row: NormalizedRow) -> str:
    """(brand, base pack size, base unit, variant) — the bucket a row may join.

    A row whose pack size or unit we could not parse gets its UNIVERSE in the key
    as well, which makes it structurally impossible to cross-universe match. Two
    listings whose sizes are both unknown are not known to be the same size; that
    is the whole reason the field is null.
    """
    base_qty, base_unit = to_base_qty(row.qty, row.unit)
    token = brand_token(row.brand, row.name)
    if base_qty is None or base_unit is None:
        return f"{token}|?|?|{(row.variant or '-').lower()}|{row.universe}"
    variant_part = (row.variant or "-").lower()
    return f"{token}|{base_qty:g}|{base_unit}|{variant_part}"


_Member = tuple[NormalizedRow, frozenset[str]]


def _place(subgroups: list[list[_Member]], member: _Member) -> None:
    """Put a row in the first subgroup whose EVERY member's name agrees with it.

    Checking every member rather than a representative keeps the guarantee the
    label depends on: within a subgroup, any two rows agree. Chaining A~B, B~C
    while A and C share nothing is precisely how a bucket ends up holding four
    different products.
    """
    _, tokens = member
    for subgroup in subgroups:
        if all(
            name_agreement(tokens, other_tokens) >= NAME_AGREEMENT_MIN
            for _, other_tokens in subgroup
        ):
            subgroup.append(member)
            return
    subgroups.append([member])


def match(rows_by_universe: dict[str, list[NormalizedRow]]) -> Comparison:
    """The v0 resolver. Deterministic, order-stable, no fuzzy matching.

    Rows from `DEMO_UNIVERSES` never enter a bucket. They are collected into
    `demo_rows` before any grouping happens, which is the only way to guarantee
    an invented price cannot end up in a row of real ones. A filter applied
    afterwards would still have let the demo row set the group's brand, pack and
    confidence on its way through.
    """
    buckets: dict[str, list[list[_Member]]] = {}
    order: list[str] = []
    total = 0
    demo_rows: list[NormalizedRow] = []

    for universe in sorted(rows_by_universe):
        if universe in DEMO_UNIVERSES:
            demo_rows.extend(rows_by_universe[universe])
            continue
        for row in rows_by_universe[universe]:
            total += 1
            key = group_key(row)
            if key not in buckets:
                buckets[key] = []
                order.append(key)
            _place(buckets[key], (row, name_tokens(row)))

    groups: list[ComparisonGroup] = []
    unmatched: list[ComparisonGroup] = []

    for key in order:
        for index, members in enumerate(buckets[key]):
            rows = [row for row, _ in members]
            universes = sorted({r.universe for r in rows})
            head = rows[0]
            base_qty, base_unit = to_base_qty(head.qty, head.unit)
            group = ComparisonGroup(
                # The subgroup index is part of the identity: one bucket can
                # legitimately split into several products.
                key=f"{key}#{index}",
                brand=head.brand,
                qty=base_qty if base_qty is not None else head.qty,
                unit=base_unit or head.unit,
                variant=head.variant,
                # One source is not a comparison. Say so rather than implying one.
                confidence="close" if len(universes) >= 2 else "unmatched",
                universes=universes,
                rows=rows,
            )
            (groups if group.confidence != "unmatched" else unmatched).append(group)

    # Cheapest-looking comparisons first; unmatched keeps payload order.
    groups.sort(key=lambda g: (-len(g.universes), g.key))

    return Comparison(
        groups=groups,
        unmatched=unmatched,
        row_count=total,
        universe_count=len(
            [u for u, r in rows_by_universe.items() if r and u not in DEMO_UNIVERSES]
        ),
        demo_rows=demo_rows,
    )
