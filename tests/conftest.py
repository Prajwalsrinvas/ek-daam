from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


def wait_for_done(client: TestClient, run_id: str, timeout: float = 15.0) -> dict:
    """Poll the snapshot until the run-level `done` event lands."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = client.get(f"/api/runs/{run_id}").json()
        if any(e["type"] == "done" for e in snapshot["events"]):
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach done within {timeout}s")


def carry_cookies(source: TestClient, target: TestClient) -> TestClient:
    """Same browser, new server process.

    A stored run is scoped to the anonymous owner cookie that created it (see
    server/owner.py), and every `TestClient` starts with an empty cookie jar. A
    test about what survives a restart has to carry the cookie across or it is
    quietly testing ownership instead of disk fallback.
    """
    target.cookies.update(source.cookies)
    return target


def make_settings(runs_dir: Path, **overrides) -> Settings:
    """Test settings: mock mode, zero delays, an isolated runs directory.

    Deliberately built by hand rather than from the environment so a developer's
    real .env can never leak into a test run. The throttles are off by default;
    the tests that are about a throttle turn their own one on.
    """
    base = Settings(
        bd_api_key="",
        bd_mode="mock",
        bd_base_url="https://api.brightdata.invalid",
        collector_ids={"zepto": "", "blinkit": "", "instamart": "", "chaos": ""},
        collector_version="dev",
        query_allowlist=(),
        max_concurrent_runs=4,
        rate_limit_per_min=100,
        run_cooldown_s=0.0,
        poll_interval_s=0.0,
        universe_timeout_s=30.0,
        mock_step_delay_s=0.0,
        replay_max_gap_s=0.0,
        runs_dir=runs_dir,
        fixtures_dir=FIXTURES,
    )
    return dataclasses.replace(base, **overrides) if overrides else base


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path / "runs")


@pytest.fixture
def zepto_fixture() -> list[dict]:
    import json

    return json.loads((FIXTURES / "zepto_collector_result.json").read_text(encoding="utf-8"))
