"""Anonymous run ownership: server/owner.py.

NOT authentication, and these tests do not pretend otherwise. What is under test
is that a public demo does not show one visitor's searches to the next one, and
that a run which is not yours is indistinguishable from a run that is not there.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from conftest import make_settings, wait_for_done
from server.app import create_app
from server.owner import COOKIE_NAME, hash_token
from server.runs import ADMIN_ATTEMPTS_PER_MIN

TOKEN = "test-chaos-token"


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def start_and_finish(client: TestClient, query: str = "amul butter") -> str:
    run_id = client.post("/api/runs", json={"query": query, "pincode": "560001"}).json()["run_id"]
    wait_for_done(client, run_id)
    return run_id


def stranger(settings) -> TestClient:
    """A second browser: its own cookie jar, so its own anonymous identity."""
    return TestClient(create_app(settings))


# -- the cookie itself -------------------------------------------------------
def test_a_request_with_no_cookie_is_given_one(client: TestClient) -> None:
    response = client.get("/api/universes")

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{COOKIE_NAME}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.replace("samesite", "SameSite")
    assert "Path=/" in set_cookie
    assert "Max-Age=31536000" in set_cookie


def test_the_cookie_is_issued_once_and_then_left_alone(client: TestClient) -> None:
    """A new cookie on every request would mean a new identity on every request,
    and no visitor would ever see their own second run."""
    first = client.get("/api/universes")
    second = client.get("/api/universes")

    assert "set-cookie" in first.headers
    assert "set-cookie" not in second.headers


def test_the_raw_cookie_is_never_stored_on_disk(client: TestClient, settings) -> None:
    run_id = start_and_finish(client)
    raw = client.cookies[COOKIE_NAME]

    meta = json.loads((settings.runs_dir / run_id / "meta.json").read_text(encoding="utf-8"))

    assert meta["owner_hash"] == hash_token(raw)
    assert raw not in (settings.runs_dir / run_id / "meta.json").read_text(encoding="utf-8")


def test_the_owner_hash_never_leaves_the_process(client: TestClient, settings) -> None:
    """It is stored, it is compared, and it is not reported. Nobody outside the
    server has a use for it, and the public demo run would otherwise serve its
    owner's hash to every visitor for nothing."""
    run_id = start_and_finish(client)

    created = client.post("/api/runs", json={"query": "brown bread", "pincode": "560001"})
    snapshot = client.get(f"/api/runs/{run_id}")
    listing = client.get("/api/runs")

    assert "owner_hash" not in created.json()["meta"]
    assert "owner_hash" not in snapshot.json()["meta"]
    assert all("owner_hash" not in run for run in listing.json()["runs"])
    # Still on disk, which is where the comparison reads it from.
    stored = json.loads((settings.runs_dir / run_id / "meta.json").read_text(encoding="utf-8"))
    assert stored["owner_hash"]


def test_the_cookie_is_not_marked_secure_over_plain_http(client: TestClient) -> None:
    """Marked Secure on http the browser would never send it back, and every
    request would look like a new visitor."""
    assert "Secure" not in client.get("/api/universes").headers["set-cookie"]


def test_a_forwarded_https_request_gets_a_secure_cookie(client: TestClient) -> None:
    """Caddy terminates TLS, so the app's own scheme is http even in production
    and the forwarded header is the only honest signal there is."""
    response = client.get("/api/universes", headers={"X-Forwarded-Proto": "https"})

    assert "Secure" in response.headers["set-cookie"]


@pytest.mark.parametrize("override,expected", [(True, True), (False, False)])
def test_the_env_override_decides_when_it_is_set(settings, override, expected) -> None:
    forced = dataclasses.replace(settings, cookie_secure=override)
    with TestClient(create_app(forced)) as client:
        # Asked over plain http, which would otherwise decide the opposite of
        # `override` in one of these two cases.
        set_cookie = client.get("/api/universes").headers["set-cookie"]

    assert ("Secure" in set_cookie) is expected


def test_a_cookie_we_did_not_issue_is_replaced_rather_than_trusted(client: TestClient) -> None:
    response = client.get("/api/universes", cookies={COOKIE_NAME: "../../etc/passwd"})

    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}=")


# -- what one visitor can see ------------------------------------------------
def test_a_visitor_sees_their_own_run(client: TestClient) -> None:
    run_id = start_and_finish(client)

    assert client.get(f"/api/runs/{run_id}").status_code == 200
    assert run_id in {r["run_id"] for r in client.get("/api/runs").json()["runs"]}


def test_one_visitors_run_is_invisible_to_another(settings, client: TestClient) -> None:
    """404 and not 403: a 403 confirms the id exists, which is the one fact a
    stranger probing for run ids is after."""
    run_id = start_and_finish(client)

    with stranger(settings) as other:
        listed = other.get("/api/runs").json()["runs"]
        snapshot = other.get(f"/api/runs/{run_id}")
        events = other.get(f"/api/runs/{run_id}/events")
        artifact = other.get(f"/api/runs/{run_id}/artifacts/zepto.png")
        replay = other.post(f"/api/replays/{run_id}")

    assert listed == []
    assert snapshot.status_code == 404
    assert events.status_code == 404
    assert artifact.status_code == 404
    assert replay.status_code == 404
    assert snapshot.json()["detail"] == other_run_detail(run_id)


def other_run_detail(run_id: str) -> str:
    """The same sentence an id that was never issued produces, word for word."""
    return f"no run {run_id!r}"


def test_a_stranger_cannot_tell_a_hidden_run_from_a_missing_one(
    settings, client: TestClient
) -> None:
    real = start_and_finish(client)
    invented = "r_20200101_000000_dead"

    with stranger(settings) as other:
        hidden = other.get(f"/api/runs/{real}")
        missing = other.get(f"/api/runs/{invented}")

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["detail"].replace(real, "X") == missing.json()["detail"].replace(
        invented, "X"
    )


def test_the_listing_shows_only_this_visitors_runs(settings, client: TestClient) -> None:
    mine = start_and_finish(client, "amul butter")

    with stranger(settings) as other:
        theirs = start_and_finish(other, "brown bread")
        their_listing = {r["run_id"] for r in other.get("/api/runs").json()["runs"]}

    my_listing = {r["run_id"] for r in client.get("/api/runs").json()["runs"]}

    assert my_listing == {mine}
    assert their_listing == {theirs}


def test_clearing_the_cookie_makes_a_new_identity(client: TestClient) -> None:
    """Stated plainly because it is the whole shape of the feature: there is no
    account behind the cookie, so losing it loses the runs."""
    run_id = start_and_finish(client)
    client.cookies.clear()

    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.get("/api/runs").json()["runs"] == []


def test_a_replay_belongs_to_whoever_asked_for_it(client: TestClient) -> None:
    run_id = start_and_finish(client)

    replay_id = client.post(f"/api/replays/{run_id}").json()["run_id"]
    wait_for_done(client, replay_id)

    assert {r["run_id"] for r in client.get("/api/runs").json()["runs"]} == {run_id, replay_id}


# -- the public demo run -----------------------------------------------------
def test_the_demo_run_is_readable_and_replayable_by_anyone(settings) -> None:
    """One run id, public BY DESIGN: the judges' one-click demo."""
    with TestClient(create_app(settings)) as author:
        run_id = start_and_finish(author)

    public = dataclasses.replace(settings, demo_run_id=run_id)
    with TestClient(create_app(public)) as visitor:
        snapshot = visitor.get(f"/api/runs/{run_id}")
        artifact = visitor.get(f"/api/runs/{run_id}/artifacts/zepto.png")
        replay = visitor.post(f"/api/replays/{run_id}")
        replay_id = replay.json()["run_id"]
        wait_for_done(visitor, replay_id)
        listing = {r["run_id"] for r in visitor.get("/api/runs").json()["runs"]}

    assert snapshot.status_code == 200
    assert artifact.status_code == 200
    assert replay.status_code == 202
    # The replay of the public run is the visitor's own, so it is theirs to see.
    # The demo run itself is not "theirs" and does not join their listing.
    assert listing == {replay_id}


def test_the_demo_run_id_is_advertised_so_the_ui_can_offer_it(settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/universes").json()["demo_run_id"] is None

    with TestClient(create_app(dataclasses.replace(settings, demo_run_id="r_1"))) as client:
        assert client.get("/api/universes").json()["demo_run_id"] == "r_1"


def test_only_the_named_run_is_public(settings) -> None:
    with TestClient(create_app(settings)) as author:
        demo = start_and_finish(author, "amul butter")
        private = start_and_finish(author, "brown bread")

    public = dataclasses.replace(settings, demo_run_id=demo)
    with TestClient(create_app(public)) as visitor:
        assert visitor.get(f"/api/runs/{demo}").status_code == 200
        assert visitor.get(f"/api/runs/{private}").status_code == 404


# -- runs from before ownership existed --------------------------------------
def test_a_run_stored_without_an_owner_still_loads(client: TestClient, settings) -> None:
    """Old captures on disk predate the field. They must not break the loader,
    and they belong to nobody: invisible in every listing, reachable by no
    visitor, unless one is named as the public demo."""
    run_id = start_and_finish(client)
    path = settings.runs_dir / run_id / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    del meta["owner_hash"]
    path.write_text(json.dumps(meta), encoding="utf-8")

    with TestClient(create_app(settings)) as fresh:
        fresh.cookies.update(client.cookies)  # the very cookie that made it
        loaded = fresh.get(f"/api/runs/{run_id}")
        listed = fresh.get("/api/runs").json()["runs"]

    from server.runs import RunManager

    assert RunManager(settings).load_meta(run_id) is not None  # it parses
    assert loaded.status_code == 404  # and belongs to nobody
    assert listed == []


def test_an_ownerless_run_can_still_be_the_public_demo(client: TestClient, settings) -> None:
    run_id = start_and_finish(client)
    path = settings.runs_dir / run_id / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8"))
    del meta["owner_hash"]
    path.write_text(json.dumps(meta), encoding="utf-8")

    public = dataclasses.replace(settings, demo_run_id=run_id)
    with TestClient(create_app(public)) as visitor:
        assert visitor.get(f"/api/runs/{run_id}").status_code == 200


# -- heal runs ---------------------------------------------------------------
def heal_settings(tmp_path, **overrides):
    base = {
        "bd_mode": "live",
        "bd_api_key": "dummy",
        "collector_ids": {"zepto": "", "blinkit": "", "instamart": "", "chaos": "c_chaos"},
        "chaos_admin_token": TOKEN,
        "heal_poll_interval_s": 0.0,
        "heal_timeout_s": 1.0,
    }
    return make_settings(tmp_path / "runs", **{**base, **overrides})


def test_a_heal_run_is_visible_only_to_the_operator_who_started_it(tmp_path) -> None:
    """The token holder watches their own heal. It is still a run, so it is
    scoped like one: no other visitor sees it in a listing or by id."""
    settings = heal_settings(tmp_path)
    with TestClient(create_app(settings)) as operator:
        started = operator.post(
            "/api/chaos/heal", json={}, headers={"X-Chaos-Token": TOKEN}
        )
        run_id = started.json()["run_id"]
        mine = {r["run_id"] for r in operator.get("/api/runs").json()["runs"]}
        with TestClient(create_app(settings)) as other:
            theirs = other.get(f"/api/runs/{run_id}")
            listing = other.get("/api/runs").json()["runs"]

    assert started.status_code == 202
    assert run_id in mine
    assert theirs.status_code == 404
    assert listing == []


# -- the chaos admin brake ---------------------------------------------------
@pytest.mark.parametrize("path", ["/api/chaos/flip", "/api/chaos/heal"])
def test_the_chaos_admin_endpoints_are_rate_limited_per_ip(tmp_path, path: str) -> None:
    """Counted whether or not the token was right, so the token cannot be
    guessed at whatever rate the network allows."""
    settings = heal_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        codes = [
            client.post(path, json={}, headers={"X-Chaos-Token": "wrong"}).status_code
            for _ in range(ADMIN_ATTEMPTS_PER_MIN + 1)
        ]

    assert codes[:ADMIN_ATTEMPTS_PER_MIN] == [401] * ADMIN_ATTEMPTS_PER_MIN
    assert codes[-1] == 429


def test_the_admin_brake_and_the_run_brake_are_separate_windows(tmp_path) -> None:
    """Sharing one window, a stranger guessing tokens would use up a real
    visitor's runs for the minute."""
    settings = heal_settings(tmp_path, rate_limit_per_min=2, bd_mode="mock")
    with TestClient(create_app(settings)) as client:
        for _ in range(ADMIN_ATTEMPTS_PER_MIN):
            client.post("/api/chaos/flip", json={}, headers={"X-Chaos-Token": "wrong"})
        started = client.post("/api/runs", json={"query": "amul butter", "pincode": "560001"})

    assert started.status_code == 202


# -- the heal request payload ------------------------------------------------
@pytest.mark.parametrize(
    "custom_input",
    [
        [{"keyword": "amul butter", "pincode": "560001"}] * 6,
        [{"keyword": "amul butter", "pincode": "560001", "url": "http://elsewhere.invalid"}],
        [{"selector": ".price"}],
        [{"keyword": "x" * 201}],
        [{"keyword": ["amul butter"]}],
    ],
)
def test_a_heal_payload_outside_the_known_shape_is_refused(tmp_path, custom_input) -> None:
    settings = heal_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/chaos/heal",
            json={"custom_input": custom_input},
            headers={"X-Chaos-Token": TOKEN},
        )

    assert response.status_code == 422


def test_the_known_shape_is_accepted(tmp_path) -> None:
    settings = heal_settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/chaos/heal",
            json={"custom_input": [{"keyword": "amul butter", "pincode": "560001"}]},
            headers={"X-Chaos-Token": TOKEN},
        )

    assert response.status_code == 202
