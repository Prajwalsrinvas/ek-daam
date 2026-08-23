"""Carts: N products at one pincode, fanned out into N ordinary runs.

The two things a cart must never do are half-start and double-charge. Half a
cart is worse than a refused one - the shopper watches four items, sees two of
them run and cannot tell whether the others failed or were never started - and a
cart is ONE user action, so it must not cost the visitor four of their per-minute
runs and four cooldowns.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from conftest import wait_for_done
from server.app import create_app

ITEMS = ["chips", "coca cola", "candles", "cake"]


@pytest.fixture
def client(settings):
    with TestClient(create_app(dataclasses.replace(settings, max_concurrent_runs=10))) as c:
        yield c


def post_cart(client: TestClient, items: list[str] | None = None, pincode: str = "560001"):
    return client.post(
        "/api/carts",
        json={"items": ITEMS if items is None else items, "pincode": pincode},
    )


# -- what a cart is -----------------------------------------------------------
def test_a_cart_starts_one_run_per_item_in_order(client: TestClient) -> None:
    created = post_cart(client)
    body = created.json()

    assert created.status_code == 202
    assert body["cart_id"].startswith("cart_")
    assert body["pincode"] == "560001"
    assert [i["item"] for i in body["items"]] == ITEMS
    assert all(i["run_id"].startswith("r_") for i in body["items"])
    assert len({i["run_id"] for i in body["items"]}) == len(ITEMS)


def test_a_carts_runs_are_ordinary_runs(client: TestClient) -> None:
    """No cart-level stream and no cart-level receipt: each item is a run the UI
    already knows how to watch, which is the whole reason a cart is cheap."""
    items = post_cart(client).json()["items"]

    for item in items:
        snapshot = wait_for_done(client, item["run_id"])
        assert snapshot["meta"]["query"] == item["item"]
        assert snapshot["meta"]["pincode"] == "560001"
        assert [e["type"] for e in snapshot["events"]][-1] == "done"


def test_a_cart_is_listed_for_its_own_browser_newest_first(client: TestClient) -> None:
    first = post_cart(client, ["chips"]).json()["cart_id"]
    second = post_cart(client, ["cake"]).json()["cart_id"]

    listed = client.get("/api/carts").json()["carts"]

    assert [c["cart_id"] for c in listed][:2] == [second, first]


def test_a_cart_is_readable_by_id(client: TestClient) -> None:
    cart_id = post_cart(client).json()["cart_id"]

    fetched = client.get(f"/api/carts/{cart_id}")

    assert fetched.status_code == 200
    assert fetched.json()["cart_id"] == cart_id


def test_the_owner_hash_never_leaves_the_process(client: TestClient, settings) -> None:
    created = post_cart(client, ["chips"]).json()
    stored = json.loads(
        (settings.runs_dir / "carts" / f"{created['cart_id']}.json").read_text(encoding="utf-8")
    )

    assert "owner_hash" not in created
    assert "owner_hash" not in client.get(f"/api/carts/{created['cart_id']}").json()
    assert stored["owner_hash"]  # stored, which is where the comparison reads it


def test_another_browser_cannot_see_a_cart(client: TestClient, settings) -> None:
    """404 and not 403, exactly as a run that is not yours is a 404: a cart id
    that exists is the one fact a stranger would be probing for."""
    cart_id = post_cart(client).json()["cart_id"]

    with TestClient(create_app(settings)) as other:
        fetched = other.get(f"/api/carts/{cart_id}")
        listed = other.get("/api/carts").json()["carts"]

    assert fetched.status_code == 404
    assert fetched.json()["detail"] == f"no cart {cart_id!r}"
    assert listed == []


def test_an_unknown_cart_id_is_a_404(client: TestClient) -> None:
    assert client.get("/api/carts/cart_20200101_000000_dead").status_code == 404
    assert client.get("/api/carts/not-a-cart-id").status_code == 404


# -- validation ---------------------------------------------------------------
def test_the_same_item_twice_is_one_run(client: TestClient) -> None:
    """"chips, Chips" is one thing a shopper wanted twice, not two of their six."""
    body = post_cart(client, ["chips", "CHIPS", " chips "]).json()

    assert [i["item"] for i in body["items"]] == ["chips"]


def test_an_empty_cart_is_refused(client: TestClient) -> None:
    response = post_cart(client, [])

    assert response.status_code == 400
    assert "at least one item" in response.json()["detail"]


def test_a_cart_over_the_limit_is_refused_with_the_limit_in_it(client: TestClient) -> None:
    response = post_cart(client, ["a" * 3 + str(n) for n in range(7)])

    assert response.status_code == 400
    assert "at most 6 items" in response.json()["detail"]


def test_the_limit_is_configurable(settings) -> None:
    two = dataclasses.replace(settings, cart_max_items=2, max_concurrent_runs=10)
    with TestClient(create_app(two)) as client:
        assert post_cart(client, ["chips", "cake"]).status_code == 202
        assert post_cart(client, ["chips", "cake", "candles"]).status_code == 400


@pytest.mark.parametrize("item", ["", "  ", "a", "b" * 61, "amul\x07butter"])
def test_an_item_is_held_to_the_same_rules_as_a_query(client: TestClient, item: str) -> None:
    """A cart must not be a way past a bound a single search is held to."""
    response = post_cart(client, ["chips", item])

    assert response.status_code == 400
    assert "query" in response.json()["detail"]


@pytest.mark.parametrize("pincode", ["", "56009", "5600011", "abcdef"])
def test_a_bad_pincode_refuses_the_whole_cart(client: TestClient, pincode: str) -> None:
    response = post_cart(client, pincode=pincode)

    assert response.status_code == 400
    assert "pincode" in response.json()["detail"]


def test_a_refused_cart_starts_nothing(client: TestClient) -> None:
    post_cart(client, ["chips", "x"])

    assert client.get("/api/runs").json()["runs"] == []
    assert client.get("/api/carts").json()["carts"] == []


# -- the brakes ---------------------------------------------------------------
def test_a_cart_that_does_not_fit_the_clamp_is_refused_whole(settings) -> None:
    """All or nothing. Starting two of four and refusing the rest leaves the
    shopper with half a comparison and no way to know it."""
    tight = dataclasses.replace(settings, max_concurrent_runs=2, mock_step_delay_s=0.2)
    with TestClient(create_app(tight)) as client:
        refused = post_cart(client, ITEMS)
        runs = client.get("/api/runs").json()["runs"]

    assert refused.status_code == 429
    assert "run slots" in refused.json()["detail"]
    assert runs == []


def test_a_cart_fits_when_the_clamp_is_wide_enough(settings) -> None:
    exact = dataclasses.replace(settings, max_concurrent_runs=4, mock_step_delay_s=0.2)
    with TestClient(create_app(exact)) as client:
        created = post_cart(client, ITEMS)
        for item in created.json()["items"]:
            wait_for_done(client, item["run_id"])

    assert created.status_code == 202


def test_a_run_in_flight_counts_against_the_carts_room(settings) -> None:
    tight = dataclasses.replace(settings, max_concurrent_runs=2, mock_step_delay_s=0.3)
    with TestClient(create_app(tight)) as client:
        single = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})
        refused = post_cart(client, ["chips", "cake"])
        wait_for_done(client, single.json()["run_id"])

    assert single.status_code == 202
    assert refused.status_code == 429


def test_the_cooldown_is_charged_once_for_the_whole_cart(settings) -> None:
    """A cart is one user action. Charging it per item would lock the visitor out
    for the cooldown four times over for one click."""
    cooled = dataclasses.replace(settings, run_cooldown_s=60.0, max_concurrent_runs=10)
    with TestClient(create_app(cooled)) as client:
        created = post_cart(client, ITEMS)
        again = post_cart(client, ["bread"])

    assert created.status_code == 202
    assert again.status_code == 429
    assert "one run per 60s" in again.json()["detail"]


def test_the_per_minute_window_is_charged_once_too(settings) -> None:
    """Same reasoning, and without it a cart of six could never be started
    against a limit of five runs a minute."""
    limited = dataclasses.replace(settings, rate_limit_per_min=2, max_concurrent_runs=10)
    with TestClient(create_app(limited)) as client:
        codes = [post_cart(client, ITEMS).status_code for _ in range(3)]

    assert codes == [202, 202, 429]


# -- replaying a cart ---------------------------------------------------------
# A cart is replayed by replaying each of its runs at once. The replay guards
# used to be per client, which let item 1 through and refused the rest, so a
# four-item demo card replayed as a one-item basket.
def finished_cart(client: TestClient, items: list[str]) -> list[str]:
    run_ids = [i["run_id"] for i in post_cart(client, items).json()["items"]]
    for run_id in run_ids:
        wait_for_done(client, run_id)
    return run_ids


def test_every_run_of_a_cart_replays_at_once(settings) -> None:
    """The replay cooldown is charged per capture, not per client, so the second,
    third and fourth items are not refused for the sin of following the first."""
    cooled = dataclasses.replace(
        settings, max_concurrent_runs=10, run_cooldown_s=60.0, replay_max_gap_s=2.0
    )
    with TestClient(create_app(cooled)) as client:
        run_ids = finished_cart(client, ITEMS)

        codes = [client.post(f"/api/replays/{run_id}").status_code for run_id in run_ids]

    assert codes == [202, 202, 202, 202]


def test_re_streaming_one_capture_twice_over_is_still_refused(settings) -> None:
    """The guard that matters is unchanged: a second re-stream of the SAME
    capture buys the client nothing and costs the process a task, a file handle
    and every event in memory."""
    paced = dataclasses.replace(settings, max_concurrent_runs=10, replay_max_gap_s=2.0)
    with TestClient(create_app(paced)) as client:
        run_ids = finished_cart(client, ["chips", "cake"])

        first = client.post(f"/api/replays/{run_ids[0]}")
        sibling = client.post(f"/api/replays/{run_ids[1]}")
        again = client.post(f"/api/replays/{run_ids[0]}")

    assert (first.status_code, sibling.status_code) == (202, 202)
    assert again.status_code == 429
    assert "already streaming" in again.json()["detail"]


def test_the_replay_ceiling_is_one_carts_worth(settings) -> None:
    """Still bounded: the cart size IS the ceiling, so the largest legitimate
    burst fits and nothing beyond it does."""
    small = dataclasses.replace(
        settings, max_concurrent_runs=10, cart_max_items=2, replay_max_gap_s=2.0
    )
    with TestClient(create_app(small)) as client:
        run_ids = finished_cart(client, ["chips", "cake"])
        extra = client.post("/api/runs", json={"query": "candles", "pincode": "560001"})
        third = extra.json()["run_id"]
        wait_for_done(client, third)

        codes = [
            client.post(f"/api/replays/{run_id}").status_code
            for run_id in [*run_ids, third]
        ]
        detail = client.post(f"/api/replays/{third}").json()["detail"]

    assert codes == [202, 202, 429]
    assert "at most 2 replays" in detail


class SlowFakeBD:
    """A collector client that never finishes, so runs stay in flight for the
    length of a test. It spends nothing: no request leaves the process."""

    mode = "live"

    async def trigger(self, collector_id, inputs, version) -> str:
        return "j_fake_1"

    async def job_log(self, job_id):
        from server.bd_client import JobLog

        return JobLog(job_id=job_id, status="running", navigations=2, lines=0)

    async def cancel_job(self, job_id) -> bool:
        return True

    async def aclose(self) -> None:
        return None


def live_carts(settings, **overrides):
    return dataclasses.replace(
        settings,
        bd_mode="live",
        bd_api_key="dummy-test-key",
        bd_base_url="https://api.brightdata.invalid",
        collector_ids={"zepto": "c_test01", "blinkit": "", "instamart": "", "chaos": ""},
        poll_interval_s=0.05,
        max_concurrent_runs=10,
        **overrides,
    )


def test_a_cart_asks_the_daily_budget_for_room_for_every_item(settings, monkeypatch) -> None:
    """The cooldown paces one visitor; this paces the collector credits, and a
    cart really does trigger one collector job per item. Asked for all at once,
    because a cart that ran out of budget halfway is a half cart."""
    from server import runs as runs_module

    monkeypatch.setattr(runs_module, "build_client", lambda s: SlowFakeBD())
    budgeted = live_carts(settings, daily_run_budget=3)

    with TestClient(create_app(budgeted)) as client:
        manager = client.app.state.runs
        refused = post_cart(client, ITEMS)  # four items, three left
        started_nothing = client.get("/api/runs").json()["runs"]
        fits = post_cart(client, ["chips", "cake", "candles"])
        used = manager._budget_used

    assert refused.status_code == 429
    assert "daily live-run budget" in refused.json()["detail"]
    assert started_nothing == []
    assert fits.status_code == 202
    assert used == 3  # one unit per item, and the day is now spent
