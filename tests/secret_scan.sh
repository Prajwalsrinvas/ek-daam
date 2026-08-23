#!/usr/bin/env bash
# Pre-commit-style secret scan (DESIGN.md §10).
#
# Nothing that looks like an API key, a cookie, a bearer token or a request
# signature may live in this repo. Real values belong in .env, which is
# gitignored; .env.example carries dummies only.
#
# Bright Data object IDs are not credentials, so this scanner focuses on values
# that grant access. Publication checks should still remove unnecessary account
# metadata such as real collector and template IDs.
#
# Usage: tests/secret_scan.sh [path]   (exit 0 = clean, 1 = found something)

set -uo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if ! command -v rg >/dev/null 2>&1; then
  echo "secret_scan: ripgrep (rg) not installed — skipping" >&2
  exit 0
fi

EXCLUDES=(
  --glob '!.git/**'
  --glob '!**/node_modules/**'
  --glob '!**/dist/**'
  --glob '!.venv/**'
  # Runtime runs are ignored and are not publication inputs.
  --glob '!runs/r_*/**'
  --glob '!runs/rp_*/**'
  --glob '!**/package-lock.json'
  --glob '!uv.lock'
  --glob '!tests/secret_scan.sh'
)

# name|regex
PATTERNS=(
  # A Bright Data file reference is `<job id>.<hash>.<file id>.<name>.<ext>`.
  # The hash inside one is part of an artifact name, not a credential, so it is
  # excluded rather than the rule being dropped: a bare 40+ hex string anywhere
  # else is still exactly what an API key looks like.
  'long hex token|\b[0-9a-f]{40,}\b(?!\.file_)'
  'bearer token|Bearer [A-Za-z0-9_.-]{20,}'
  'openai-style key|\b(sk|pk)-[A-Za-z0-9]{20,}\b'
  'aws access key|\bAKIA[0-9A-Z]{16}\b'
  'cookie header|(?i)(set-)?cookie:\s*\S'
  # Only flagged when something is actually assigned to them — prose that merely
  # names the field is not a leak. `store_ids` is deliberately NOT in this list:
  # a store id is visible to any logged-out browser, and `validated` events now
  # record one as provenance.
  'session or signature value|(?i)\b(x-csrf-secret|request-signature|session_id|userSessionId)\b\s*[:=]\s*["'"'"']?[A-Za-z0-9_-]{8,}'
  # `<...>` is a documentation placeholder, never a real value — a Bright Data
  # token is hex and cannot start with `<`. Allowing it keeps the scan usable in
  # docs (docs/RUNBOOK.md) without letting anything real through.
  'populated api key|\bBD_API_KEY\s*=\s*(?!$|bd_dummy|<)\S'
)

status=0
errors=$(mktemp)
trap 'rm -f "$errors"' EXIT

for entry in "${PATTERNS[@]}"; do
  name="${entry%%|*}"
  pattern="${entry#*|}"
  hits=$(rg --pcre2 --no-heading --line-number --color never "${EXCLUDES[@]}" -e "$pattern" "$ROOT" 2>"$errors")
  rc=$?
  # rg: 0 = matched, 1 = no match, anything else = the scan itself broke (a bad
  # pattern, an unreadable path, no PCRE2 support). Swallowing that reported a
  # clean repo for a scan that never ran, which is the one failure mode a secret
  # scan must not have.
  if [ "$rc" -gt 1 ]; then
    echo "secret_scan: rg failed (exit $rc) on rule '$name' — the scan did NOT run" >&2
    cat "$errors" >&2
    status=1
    continue
  fi
  if [ -n "$hits" ]; then
    echo "secret_scan: possible $name" >&2
    echo "$hits" >&2
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  echo "secret_scan: clean"
fi
exit "$status"
