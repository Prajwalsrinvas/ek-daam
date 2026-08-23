"""The repo must not carry a secret. DESIGN.md §10 makes this a hard rule, so it
is a test rather than a checklist item."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tests" / "secret_scan.sh"


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_repo_has_no_secrets() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), str(REPO_ROOT)], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, f"secret scan failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_scan_actually_catches_a_planted_key(tmp_path: Path) -> None:
    """A scan that can never fail is not a scan."""
    # Assembled rather than written out, so this file does not itself trip the scan.
    fake_key = "b5648e10" * 8
    (tmp_path / "leak.py").write_text(f'API_KEY = "{fake_key}"\n', encoding="utf-8")

    result = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 1
    assert "long hex token" in result.stderr


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_bright_data_ids_are_not_treated_as_secrets(tmp_path: Path) -> None:
    """A collector / job / template id names a job inside one account and does
    nothing without the API key. They also ride legitimately in a committed
    replay's `triggered` events, so flagging them made the scan permanently red
    — and a check that is always red stops being read."""
    (tmp_path / "events.jsonl").write_text(
        '{"type":"triggered","data":{"job_id":"j_mt5eiq82129bos58em"}}\n'
        '{"collector":"c_mt4h9xk21ab7cde90f","template":"t_mt4h9xk21ab7cde90f"}\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_a_bright_data_file_reference_is_not_a_long_hex_token(tmp_path: Path) -> None:
    """`<job id>.<hash>.<file id>.<name>.png` — the hash is part of an artifact
    name, not a credential. The rule still has to catch a bare hex key, so it is
    narrowed rather than dropped."""
    ref = "j_mt5eiq7ibyysxr3x5." + "e2c5c45b" * 5 + ".file_mt5emv22c58zzt2h5.serp_screenshot.png"
    (tmp_path / "raw.json").write_text(f'{{"serp_screenshot": "{ref}"}}\n', encoding="utf-8")

    result = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_a_store_id_in_an_event_is_not_a_secret(tmp_path: Path) -> None:
    """`validated` events record the store the collector read as provenance. Any
    logged-out browser sees the same id, so the scan must not block committing a
    replay that carries one."""
    (tmp_path / "events.jsonl").write_text(
        '{"type":"validated","data":{"store_ids":["1382868"],"known_store":null}}\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_doc_placeholders_pass_but_a_real_looking_key_does_not(tmp_path: Path) -> None:
    """`BD_API_KEY=<real key>` in a runbook is not a leak; an actual value is."""
    (tmp_path / "runbook.md").write_text("BD_API_KEY=<real key>\n", encoding="utf-8")
    clean = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True, timeout=60
    )

    # Split so this file does not itself carry a flagged assignment.
    (tmp_path / "leak.env").write_text("BD_API" + "_KEY=abc123realvalue\n", encoding="utf-8")
    dirty = subprocess.run(
        ["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True, timeout=60
    )

    assert clean.returncode == 0, clean.stderr
    assert dirty.returncode == 1
    assert "populated api key" in dirty.stderr


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_a_scan_that_could_not_run_is_reported_as_a_failure(tmp_path: Path) -> None:
    """`rg` exits 2 when the scan itself broke — an unreadable path, a pattern it
    cannot compile. Swallowing that printed "clean" for a scan that never looked
    at anything, which is the one failure mode a secret scan must not have."""
    missing = tmp_path / "not-a-directory"

    result = subprocess.run(
        ["bash", str(SCRIPT), str(missing)], capture_output=True, text=True, timeout=60
    )

    assert result.returncode == 1
    assert "the scan did NOT run" in result.stderr
    assert "clean" not in result.stdout


def test_env_example_carries_only_dummies() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "BD_API_KEY=bd_dummy_key_replace_me" in text
    for name in ("ZEPTO", "BLINKIT", "INSTAMART", "CHAOS"):
        assert f"SVERSE_COLLECTOR_{name}=\n" in text


# A decimal-degree pair on one line. Four or more decimal places is the tell —
# that is street-level precision, not a version number or a price. Deliberately a
# SHAPE, not a value: a guard that spells out the coordinates it is looking for
# has published them. (No example is written here for the same reason; the
# planted-pair test below builds one at runtime.)
COORDINATE_PAIR = r"\b[0-9]{1,3}\.[0-9]{4,}\s*,\s*[0-9]{1,3}\.[0-9]{4,}\b"

# A single coordinate assigned to a lat/long-shaped name, for the case where the
# pair is split across two lines or two fields. Prose naming the fields (the
# runbook says a collector requiring `lat`/`long` will 422) carries no value and
# is the opposite of a leak, so an assignment is required.
COORDINATE_FIELD = (
    r"""(?i)\b(lat|lng|long|latitude|longitude)\b["']?\s*[:=]\s*["']?-?[0-9]{1,3}\.[0-9]{3,}"""
)

# Either removed variable being ASSIGNED. Prose naming them (the runbook says to
# delete them from an old `.env`) is not a leak.
PINCODE_TABLE = r"SVERSE_PINCODE_(MAP|ALLOWLIST)\s*="


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_no_coordinates_or_pincode_table_in_the_code_or_docs() -> None:
    """The app used to carry a pincode -> lat/long table. Nothing reads
    coordinates now, so nothing may carry them — a coordinate pair is a real
    address, and this test is what keeps one from creeping back.

    Matched by SHAPE, never by value: writing the coordinates being guarded
    against into the guard would commit the very thing it exists to keep out.

    Runtime runs are ignored and skipped. Any future demo replay must live in a
    tracked publication directory so the normal repository scan covers it.
    """
    hits = subprocess.run(
        [
            "rg", "--pcre2", "--no-heading", "--line-number", "--color", "never",
            "--glob", "!.git/**", "--glob", "!**/node_modules/**",
            "--glob", "!**/dist/**", "--glob", "!.venv/**",
            "--glob", "!uv.lock", "--glob", "!**/package-lock.json",
            "--glob", "!runs/r_*/**", "--glob", "!runs/rp_*/**",
            "-e", COORDINATE_PAIR,
            "-e", COORDINATE_FIELD,
            "-e", PINCODE_TABLE,
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert hits.stdout == "", f"coordinates or a pincode table are still committed:\n{hits.stdout}"


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_the_coordinate_guard_catches_a_planted_pair(tmp_path: Path) -> None:
    """A guard that can never fire is not a guard.

    The plant is assembled from parts so this file does not itself carry a
    coordinate pair — the same trick the planted-key test uses.
    """
    planted = "12" + ".9716, " + "77" + ".5946"
    (tmp_path / "notes.md").write_text(f"the store is at {planted}\n", encoding="utf-8")
    (tmp_path / "collector.js").write_text(
        "const lat = 1" + "3.0696;\n", encoding="utf-8"
    )

    for pattern in (COORDINATE_PAIR, COORDINATE_FIELD):
        hits = subprocess.run(
            ["rg", "--pcre2", "--no-heading", "--color", "never", "-e", pattern, str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert hits.stdout != "", f"pattern did not fire on a planted coordinate: {pattern}"


def test_gitignore_covers_env_and_runs() -> None:
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".env\n" in text
    assert "runs/\n" in text
    assert "DESIGN.md\n" in text
