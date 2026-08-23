#!/usr/bin/env bash
# chaos-monkey: redesign the demo store on command, and ask Bright Data to
# repair the collector that reads it.
#
# With no flag it flips the store to its next rendering, which is what breaks
# the chaos collector. With --heal it starts a self-heal run instead and prints
# the run id to watch. The admin token is read from the environment; no value
# belongs in this file.
#
# Usage:
#   CHAOS_ADMIN_TOKEN=<token> ./chaos-monkey.sh
#   CHAOS_ADMIN_TOKEN=<token> ./chaos-monkey.sh --heal
#   EKDAAM_URL=http://localhost:8000 CHAOS_ADMIN_TOKEN=<token> ./chaos-monkey.sh

set -euo pipefail

BASE="${EKDAAM_URL:-https://ekdaam.duckdns.org}"
BASE="${BASE%/}"

HEAL=0
for arg in "$@"; do
  case "$arg" in
    --heal) HEAL=1 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "chaos-monkey: unknown argument '$arg' (try --heal or --help)" >&2; exit 2 ;;
  esac
done

if [ -z "${CHAOS_ADMIN_TOKEN:-}" ]; then
  echo "chaos-monkey: set CHAOS_ADMIN_TOKEN to the store's admin token" >&2
  echo "              (the server reads the same value as SVERSE_CHAOS_ADMIN_TOKEN)" >&2
  exit 1
fi

body=""

# One request, with the status code checked rather than assumed: a 401 that
# printed nothing would look exactly like a flip that worked.
request() {
  local method="$1" path="$2"
  shift 2
  local response code
  response=$(curl -sS -X "$method" "$BASE$path" "$@" -w $'\n%{http_code}')
  code="${response##*$'\n'}"
  body="${response%$'\n'*}"
  case "$code" in
    2*) return 0 ;;
    *)
      echo "chaos-monkey: $method $path answered $code" >&2
      echo "              $body" >&2
      return 1
      ;;
  esac
}

# One JSON field, so the script needs nothing installed beyond curl.
field() {
  printf '%s' "$2" |
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\{0,1\}\([^\",}]*\)\"\{0,1\}.*/\1/p" |
    head -n 1
}

request GET /api/chaos
before=$(field version "$body")
products=$(field products "$body")
echo "store at $BASE"
echo "  now serving: $before  ($products products)"

if [ "$HEAL" -eq 1 ]; then
  request POST /api/chaos/heal \
    -H "X-Chaos-Token: $CHAOS_ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{}'
  echo "  self-heal run: $(field run_id "$body")"
  echo "  watch it in the event feed; the template is only published once heal_promoted lands"
  exit 0
fi

# No version in the body means "advance to the next one", which wraps, so the
# demo can be run twice.
request POST /api/chaos/flip \
  -H "X-Chaos-Token: $CHAOS_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{}'
echo "  flipped: $(field previous "$body") -> $(field version "$body")"
echo "  run a search again, then trigger heal with --heal"
