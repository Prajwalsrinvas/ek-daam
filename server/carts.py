"""Carts: one shopping list at one pincode, fanned out into one run per item.

A cart is NOT a new kind of run. Every item becomes an ordinary run through the
same path `POST /api/runs` uses - same owner cookie, same guards, same events,
same receipt - and the cart itself is only the note that says which runs were
started together and what each of them was for. That is why there is no
cart-level SSE and no cart-level comparison: the UI subscribes to each run's own
stream, and a cart is replayed by replaying its runs.

Stored beside the runs, under `runs/carts/`, one JSON file per cart, carrying the
same `owner_hash` a run carries. NOT authentication - see server/owner.py.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .config import Settings

log = logging.getLogger("scrapeverse.carts")

CART_ID_RE = re.compile(r"^cart_\d{8}_\d{6}_[0-9a-f]{4}$")

CARTS_DIRNAME = "carts"


class CartItem(BaseModel):
    """One line of the list and the run that answers it."""

    item: str
    run_id: str


class CartMeta(BaseModel):
    cart_id: str
    pincode: str
    created_at: str
    items: list[CartItem] = Field(default_factory=list)
    # SHA-256 of the visitor's cookie, exactly as a run stores it, and never the
    # cookie itself. None belongs to nobody.
    owner_hash: str | None = None


def new_cart_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"cart_{stamp}_{secrets.token_hex(2)}"


def carts_dir(settings: Settings) -> Path:
    return settings.runs_dir / CARTS_DIRNAME


def cart_path(settings: Settings, cart_id: str) -> Path | None:
    """Where one cart is stored, or None if that is not a cart id.

    The id is matched against its own shape before it is ever joined onto a
    path: this is the one place a caller's string becomes a filename.
    """
    if not CART_ID_RE.match(cart_id):
        return None
    return carts_dir(settings) / f"{cart_id}.json"


def write_cart(settings: Settings, cart: CartMeta) -> None:
    path = cart_path(settings, cart.cart_id)
    if path is None:  # pragma: no cover - ids are minted by `new_cart_id`
        raise ValueError(f"not a cart id: {cart.cart_id!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cart.model_dump_json(indent=2), encoding="utf-8")


def load_cart(settings: Settings, cart_id: str) -> CartMeta | None:
    path = cart_path(settings, cart_id)
    if path is None or not path.is_file():
        return None
    try:
        return CartMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("cart %s could not be read: %s", cart_id, exc)
        return None


def list_carts(settings: Settings, owner_hash: str | None, limit: int = 25) -> list[CartMeta]:
    """This visitor's carts, newest first.

    Scoped the same way the run listing is, and for the same reason: a cart is a
    shopping list, which is a more personal thing than a single search.
    """
    directory = carts_dir(settings)
    if not directory.is_dir() or not owner_hash:
        return []
    mine: list[CartMeta] = []
    for path in directory.glob("cart_*.json"):
        try:
            cart = CartMeta.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if cart.owner_hash and cart.owner_hash == owner_hash:
            mine.append(cart)
    return sorted(mine, key=lambda c: c.created_at, reverse=True)[:limit]


def public_cart(cart: CartMeta) -> dict:
    """A cart as the API reports it, with the owner hash removed. Same rule as a
    run's meta: a value nobody outside the process reads is not published."""
    payload = cart.model_dump()
    payload.pop("owner_hash", None)
    return payload
