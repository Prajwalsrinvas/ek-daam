"""The live Bright Data seam, exercised against a fake transport.

M2 flips `BD_MODE=live` with no code change, so the request shapes this pass
commits to are worth pinning down now. Endpoint shapes come from the Bright Data
docs: `POST /dca/trigger`, `GET /dca/log/{job_id}`, `GET /dca/dataset?id=`,
`POST /dca/jobs/{job_id}/cancel`.
"""

from __future__ import annotations

import dataclasses
import json

import httpx
import pytest

from server.bd_client import BDError, LiveClient, MockClient, build_client, placeholder_png


@pytest.fixture
def live_settings(settings):
    return dataclasses.replace(
        settings, bd_mode="live", bd_api_key="dummy-test-key",
        bd_base_url="https://api.brightdata.invalid",
    )


def recording_transport(handler):
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), seen


async def test_trigger_posts_an_array_and_returns_the_collection_id(live_settings) -> None:
    transport, seen = recording_transport(
        lambda r: httpx.Response(200, json={"collection_id": "j_test01", "start_eta": "2026-05-22T13:26:22.702Z"})
    )
    client = LiveClient(live_settings, transport=transport)

    job_id = await client.trigger("c_test01", [{"keyword": "amul butter", "pincode": "560001"}], "dev")
    await client.aclose()

    request = seen[0]
    assert job_id == "j_test01"
    assert request.method == "POST"
    assert request.url.path == "/dca/trigger"
    assert request.url.params["collector"] == "c_test01"
    assert request.url.params["version"] == "dev"  # dev only when asked for
    assert request.headers["authorization"] == "Bearer dummy-test-key"
    assert json.loads(request.content) == [{"keyword": "amul butter", "pincode": "560001"}]


async def test_prod_version_is_not_sent_as_dev(live_settings) -> None:
    transport, seen = recording_transport(lambda r: httpx.Response(200, json={"collection_id": "j_test02"}))
    client = LiveClient(live_settings, transport=transport)

    await client.trigger("c_test01", [{}], "prod")
    await client.aclose()

    assert "version" not in seen[0].url.params


async def test_job_log_reads_progress_fields(live_settings) -> None:
    transport, _ = recording_transport(
        lambda r: httpx.Response(
            200,
            json={
                "id": "j_test03", "status": "running", "pages": 3, "pages_left": 2,
                "lines": 0, "fails": 0, "success": 0, "template": "t_x.1",
            },
        )
    )
    client = LiveClient(live_settings, transport=transport)

    log = await client.job_log("j_test03")
    await client.aclose()

    assert (log.status, log.pages, log.pages_left) == ("running", 3, 2)
    assert log.finished is False and log.failed is False


async def test_job_log_reads_navigations(live_settings) -> None:
    """A job Bright Data accepted, reports as running, and never allocated a
    worker to. `runs.py` watches this field precisely because the status does not
    say it: the job looks alive and has opened nothing."""
    transport, _ = recording_transport(
        lambda r: httpx.Response(
            200, json={"id": "j_stalled", "status": "running", "navigations": 0, "lines": 0}
        )
    )
    client = LiveClient(live_settings, transport=transport)

    log = await client.job_log("j_stalled")
    await client.aclose()

    assert (log.navigations, log.lines) == (0, 0)
    assert log.finished is False


async def test_navigations_absent_from_the_payload_is_none_not_zero(live_settings) -> None:
    """Nothing observed is nothing claimed. A missing field is not a reading of
    zero, and the two are distinguishable at the call site."""
    transport, _ = recording_transport(
        lambda r: httpx.Response(200, json={"id": "j", "status": "running"})
    )
    client = LiveClient(live_settings, transport=transport)

    log = await client.job_log("j")
    await client.aclose()

    assert log.navigations is None


async def test_cancel_job_posts_to_the_cancel_endpoint(live_settings) -> None:
    transport, seen = recording_transport(lambda r: httpx.Response(200, json={"done": 1}))
    client = LiveClient(live_settings, transport=transport)

    ok = await client.cancel_job("j_test09")
    await client.aclose()

    assert ok is True
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/dca/jobs/j_test09/cancel"
    assert seen[0].headers["authorization"] == "Bearer dummy-test-key"


async def test_a_refused_cancel_is_reported_not_raised(live_settings) -> None:
    """Every caller is already handling something that went wrong. A cancel that
    fails is not a second failure to report, so it comes back as False."""
    transport, _ = recording_transport(lambda r: httpx.Response(404, text="no such job"))
    client = LiveClient(live_settings, transport=transport)

    ok = await client.cancel_job("j_gone")
    await client.aclose()

    assert ok is False


@pytest.mark.parametrize(
    ("status", "finished", "failed"),
    [("building", False, False), ("running", False, False), ("done", True, False),
     ("failed", True, True), ("cancelled", True, True)],
)
async def test_job_status_vocabulary(live_settings, status, finished, failed) -> None:
    transport, _ = recording_transport(lambda r: httpx.Response(200, json={"id": "j", "status": status}))
    client = LiveClient(live_settings, transport=transport)

    log = await client.job_log("j")
    await client.aclose()

    assert (log.finished, log.failed) == (finished, failed)


async def test_dataset_202_means_still_building_not_empty(live_settings) -> None:
    transport, seen = recording_transport(
        lambda r: httpx.Response(202, json={"status": "building", "message": "not ready yet"})
    )
    client = LiveClient(live_settings, transport=transport)

    result = await client.fetch_results("j_test04")
    await client.aclose()

    assert result is None  # None is "wait", not "no rows"
    assert seen[0].url.path == "/dca/dataset"
    assert seen[0].url.params["id"] == "j_test04"


async def test_dataset_200_returns_the_record_array(live_settings) -> None:
    transport, _ = recording_transport(lambda r: httpx.Response(200, json=[{"universe": "zepto"}]))
    client = LiveClient(live_settings, transport=transport)

    result = await client.fetch_results("j_test05")
    await client.aclose()

    assert result == [{"universe": "zepto"}]


async def test_empty_dataset_is_zero_rows_not_a_wait(live_settings) -> None:
    transport, _ = recording_transport(lambda r: httpx.Response(200, json=[]))
    client = LiveClient(live_settings, transport=transport)

    result = await client.fetch_results("j_test06")
    await client.aclose()

    assert result == []


async def test_http_errors_surface_as_bderror(live_settings) -> None:
    transport, _ = recording_transport(lambda r: httpx.Response(422, text="input schema mismatch"))
    client = LiveClient(live_settings, transport=transport)

    with pytest.raises(BDError, match="422"):
        await client.trigger("c_test01", [{}], "dev")
    await client.aclose()


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"screenshot_url": "not-a-url"},
        {"screenshot_url": "https://api.brightdata.invalid/artifacts/shot.png"},
        {"screenshot_url": "https://someone-elses-bucket.example.com/shot.png"},
        {"serp_screenshot": {"__type__": "file", "url": "https://zepto.invalid/search"}},
    ],
)
async def test_the_live_client_never_downloads_a_screenshot(live_settings, record) -> None:
    """Bright Data does not deliver collector media over the API, so there is
    nothing to download and no request to make.

    The old code fetched any http(s) URL it found in a collector record — an
    unauthenticated GET, from inside our own server, to an address taken out of
    scraped third-party data. Nothing has ever produced such a URL, so it fetched
    nothing while carrying that risk. It is deleted rather than guarded: not
    making the request is the only fix that cannot be got wrong. `runs.py` turns
    the missing capture into a non-terminal `artifact_failed`.
    """
    transport, seen = recording_transport(lambda r: httpx.Response(200, content=b"png"))
    client = LiveClient(live_settings, transport=transport)

    assert await client.fetch_screenshot(record, "zepto") is None
    await client.aclose()

    assert seen == []


def test_live_mode_without_a_key_refuses_to_start(live_settings) -> None:
    keyless = dataclasses.replace(live_settings, bd_api_key="")

    with pytest.raises(BDError, match="BD_API_KEY"):
        LiveClient(keyless)


def test_build_client_follows_the_mode(settings, live_settings) -> None:
    assert isinstance(build_client(settings), MockClient)
    assert build_client(settings).mode == "mock"
    assert isinstance(build_client(live_settings), LiveClient)


async def test_mock_driver_reaches_done_in_three_polls(settings) -> None:
    client = MockClient(settings)
    job_id = await client.trigger("", [{"universe": "zepto"}], "dev")

    assert await client.fetch_results(job_id) is None  # nothing before the job finishes
    pages_left = [(await client.job_log(job_id)).pages_left for _ in range(3)]
    results = await client.fetch_results(job_id)

    assert pages_left == [2, 1, 0]
    assert isinstance(results, list) and results[0]["universe"] == "zepto"


async def test_mock_without_a_fixture_says_so(settings) -> None:
    client = MockClient(settings)
    job_id = await client.trigger("", [{"universe": "blinkit"}], "dev")
    for _ in range(3):
        await client.job_log(job_id)

    with pytest.raises(BDError, match="no mock fixture"):
        await client.fetch_results(job_id)


def test_placeholder_png_is_a_real_png() -> None:
    blob = placeholder_png((107, 33, 168), width=8, height=4)

    assert blob.startswith(b"\x89PNG\r\n\x1a\n")
    assert blob.endswith(b"IEND\xaeB`\x82")
    assert placeholder_png((1, 2, 3)) == placeholder_png((1, 2, 3))  # deterministic


# -- config sanity: a value that would break the app is corrected, loudly ------
@pytest.mark.parametrize(
    ("env", "attr", "bad", "expected"),
    [
        ("SVERSE_POLL_INTERVAL_S", "poll_interval_s", "0", 2.5),
        ("SVERSE_UNIVERSE_TIMEOUT_S", "universe_timeout_s", "0", 180.0),
        ("SVERSE_MAX_CONCURRENT_RUNS", "max_concurrent_runs", "0", 1),
        ("SVERSE_RATE_LIMIT_PER_MIN", "rate_limit_per_min", "0", 5),
    ],
)
def test_a_config_value_that_would_break_the_app_is_clamped(
    monkeypatch, env: str, attr: str, bad: str, expected: float
) -> None:
    """Each of these has a silent failure mode: a zero poll interval is a busy
    loop against /dca/log, a zero timeout times every universe out before it
    starts, and a zero concurrency cap refuses every run with "a run is already
    in flight" when none is."""
    from server.config import build_settings

    monkeypatch.setattr("server.config.load_dotenv", lambda path: None)
    monkeypatch.setenv(env, bad)

    assert getattr(build_settings(), attr) == expected


def test_the_default_collector_version_is_prod(monkeypatch) -> None:
    """`dev` is not a safe default. A collector's delivered fields are synced
    from the template's output schema only on a save to production, so a
    dev-version run can deliver a truncated projection — which reaches the app as
    a shape mismatch and reports zero_rows{broken}, i.e. "the store had
    nothing"."""
    from server.config import build_settings

    monkeypatch.setattr("server.config.load_dotenv", lambda path: None)
    monkeypatch.delenv("SVERSE_COLLECTOR_VERSION", raising=False)

    assert build_settings().collector_version == "prod"


async def test_bd_error_text_is_one_short_line(live_settings) -> None:
    """An event's `error` is one line in the feed. An HTML error page pasted into
    it wrapped the whole run log — and the HTTP status is the fact worth
    keeping."""
    body = "<html>\n  <body>\n    " + ("x" * 500) + "\n  </body>\n</html>"
    transport, _ = recording_transport(lambda r: httpx.Response(502, text=body))
    client = LiveClient(live_settings, transport=transport)

    with pytest.raises(BDError) as caught:
        await client.trigger("c_test01", [{}], "prod")
    await client.aclose()

    message = str(caught.value)
    assert "502" in message
    assert "\n" not in message
    assert len(message) < 160
