// The event fold. DESIGN.md §8 calls this THE core UI logic, so it is a pure
// function of (state, event) with no React, no fetch and no clock in it.
//
// ADDING A SERVER EVENT TYPE takes THREE edits here, not one:
//   1. the `EventType` union in types.ts
//   2. `IMPLEMENTED_EVENT_TYPES` below — it is the SSE subscription list, and a
//      named frame missing from it is dropped silently with no error anywhere
//   3. a `case` in `foldEvent` and a line in `describe`
// See docs/RUNBOOK.md.

import type { EventType, RunEvent, RunMeta } from "./types";

/** Every event type the server actually emits today. This array is what
 *  `App.tsx` subscribes to: the server names every SSE frame (`event: rows`),
 *  and a named frame never reaches `EventSource.onmessage`, so anything absent
 *  here is invisible to the UI. */
export const IMPLEMENTED_EVENT_TYPES = [
  "run_requested",
  "universe_dispatched",
  "triggered",
  "progress",
  "rows",
  "screenshot",
  "artifact_failed",
  "validated",
  "zero_rows",
  "retriggered",
  "failed",
  "timed_out",
  "done",
  // The self-heal cycle, emitted by a heal run (POST /api/chaos/heal).
  "heal_started",
  "heal_previewed",
  "heal_approved",
  "heal_promoted",
] as const satisfies readonly EventType[];

export type UniverseStatus =
  | "idle"
  | "dispatched"
  | "triggered"
  | "collecting"
  | "rows"
  | "validated"
  | "zero_rows"
  | "failed"
  | "timed_out";

export interface UniverseState {
  id: string;
  status: UniverseStatus;
  jobId: string | null;
  pagesLeft: number | null;
  rows: number | null;
  rowsKept: number | null;
  rowsDropped: number | null;
  dropReasons: Record<string, number>;
  zeroReason: string | null;
  error: string | null;
  /** A page capture we could not fetch. Non-terminal — the rows still stand, so
   *  this never changes `status`. */
  artifactError: string | null;
  screenshot: { artifact: string; url: string; placeholder: boolean } | null;
  lastEventAt: string | null;
}

export interface RunState {
  runId: string | null;
  query: string | null;
  pincode: string | null;
  areaLabel: string | null;
  mode: string | null;
  replay: boolean;
  done: boolean;
  /** A RUN-level failure (`universe: null`), as opposed to one universe's. The
   *  run is over and there will be no `done`, so this is the only thing that
   *  will ever say why. */
  error: string | null;
  lastEventId: number;
  order: string[];
  universes: Record<string, UniverseState>;
  feed: RunEvent[];
}

export const MAX_FEED = 400;

export function emptyRunState(): RunState {
  return {
    runId: null,
    query: null,
    pincode: null,
    areaLabel: null,
    mode: null,
    replay: false,
    done: false,
    error: null,
    lastEventId: 0,
    order: [],
    universes: {},
    feed: [],
  };
}

export function stateFromMeta(meta: RunMeta): RunState {
  return {
    ...emptyRunState(),
    runId: meta.run_id,
    query: meta.query,
    pincode: meta.pincode,
    areaLabel: meta.area_label,
    mode: meta.mode,
    replay: meta.replay,
  };
}

function blankUniverse(id: string): UniverseState {
  return {
    id,
    status: "idle",
    jobId: null,
    pagesLeft: null,
    rows: null,
    rowsKept: null,
    rowsDropped: null,
    dropReasons: {},
    zeroReason: null,
    error: null,
    artifactError: null,
    screenshot: null,
    lastEventAt: null,
  };
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function foldEvent(state: RunState, event: RunEvent): RunState {
  // Events are strictly ordered by `i`; anything we have already folded (a
  // reconnect overlap, say) is dropped rather than double-counted.
  if (event.i <= state.lastEventId) return state;

  const next: RunState = {
    ...state,
    runId: state.runId ?? event.run_id,
    replay: state.replay || event.replay,
    lastEventId: event.i,
    feed: [...state.feed, event].slice(-MAX_FEED),
    universes: { ...state.universes },
    order: state.order,
  };

  const uid = event.universe;
  if (uid && !next.universes[uid]) {
    next.universes[uid] = blankUniverse(uid);
    next.order = [...state.order, uid];
  }

  const u = uid ? { ...next.universes[uid], lastEventAt: event.ts } : null;
  const d = event.data ?? {};

  switch (event.type) {
    case "run_requested":
      next.query = str(d.query) ?? next.query;
      next.pincode = str(d.pincode) ?? next.pincode;
      next.areaLabel = str(d.area_label) ?? next.areaLabel;
      next.mode = str(d.mode) ?? next.mode;
      break;
    case "universe_dispatched":
      if (u) u.status = "dispatched";
      break;
    case "triggered":
      if (u) {
        u.status = "triggered";
        u.jobId = str(d.job_id);
      }
      break;
    case "progress":
      if (u) {
        u.status = "collecting";
        u.pagesLeft = num(d.pages_left);
      }
      break;
    case "rows":
      if (u) {
        u.status = "rows";
        u.rows = num(d.n);
        u.pagesLeft = 0;
      }
      break;
    case "screenshot":
      if (u) {
        u.screenshot = {
          artifact: str(d.artifact) ?? "",
          url: str(d.url) ?? "",
          placeholder: d.placeholder === true,
        };
        u.artifactError = null;
      }
      break;
    case "artifact_failed":
      // Non-terminal on purpose: `status` is left alone so a universe whose rows
      // are fine keeps reading as healthy. The missing capture is still recorded
      // because the capture is evidence.
      if (u) u.artifactError = str(d.error);
      break;
    case "validated":
      if (u) {
        u.status = "validated";
        u.rowsKept = num(d.rows_kept);
        u.rowsDropped = num(d.rows_dropped);
        u.dropReasons = (d.reasons as Record<string, number>) ?? {};
      }
      break;
    case "zero_rows":
      if (u) {
        u.status = "zero_rows";
        u.zeroReason = str(d.reason);
      }
      break;
    case "retriggered":
      // Non-terminal and deliberately status-preserving: the job that never
      // started is gone and a replacement is already in flight, so the universe
      // is still collecting and must not read as broken. The `triggered` event
      // that follows carries the new job id; this one names the abandoned job.
      break;
    case "failed":
      if (u) {
        u.status = "failed";
        u.error = str(d.error);
      } else {
        // Run-level. The server emits this INSTEAD of `done`, so without
        // settling here the UI waited forever on a run that was already over —
        // spinner up, no reason shown, and the EventSource reconnecting against
        // a closed stream.
        next.done = true;
        next.error = str(d.error) ?? "the run failed";
      }
      break;
    case "timed_out":
      if (u) {
        u.status = "timed_out";
      } else {
        next.done = true;
        next.error = `the run timed out${d.after_s ? ` after ${d.after_s}s` : ""}`;
      }
      break;
    case "done":
      next.done = true;
      break;
    default:
      break;
  }

  if (uid && u) next.universes[uid] = u;
  return next;
}

export function foldAll(state: RunState, events: RunEvent[]): RunState {
  return events.reduce(foldEvent, state);
}

const TERMINAL: UniverseStatus[] = ["validated", "zero_rows", "failed", "timed_out"];

export function isSettled(u: UniverseState): boolean {
  return TERMINAL.includes(u.status);
}

/** One terse human line per event. No invented adjectives — just what happened. */
export function describe(event: RunEvent): string {
  const d = event.data ?? {};
  switch (event.type) {
    case "run_requested":
      // A run captured before the pincode table was removed carries a real area
      // label; a new one's label IS its pincode, so it is not printed twice.
      return d.area_label && d.area_label !== d.pincode
        ? `"${d.query}" at ${d.area_label} (${d.pincode})`
        : `"${d.query}" at ${d.pincode}`;
    case "universe_dispatched":
      return `dispatched ${d.display ?? event.universe}`;
    case "triggered":
      return `collector job ${d.job_id} (version ${d.version})`;
    case "progress":
      // A scrape job counts pages; a self-heal job names the stage it is on.
      return d.step ? `step ${d.step}` : `${d.pages_left} page(s) left`;
    case "rows":
      return `${d.n} row(s) parsed`;
    case "screenshot":
      return `screenshot ${d.artifact}${d.placeholder ? " (placeholder)" : ""}`;
    case "artifact_failed":
      return `no page capture — ${d.error} (rows unaffected)`;
    case "validated": {
      const reasons = Object.entries((d.reasons as Record<string, number>) ?? {})
        .map(([k, v]) => `${k}×${v}`)
        .join(", ");
      return `${d.rows_kept} kept, ${d.rows_dropped} dropped${reasons ? ` (${reasons})` : ""}`;
    }
    case "zero_rows":
      return `no usable rows — ${d.reason}`;
    case "retriggered":
      return `collector job never started, canceled and retriggered after ${d.after_s}s`;
    case "failed":
      return event.universe ? `failed — ${d.error}` : `run failed — ${d.error}`;
    case "timed_out":
      return event.universe
        ? `timed out after ${d.after_s}s`
        : `run timed out after ${d.after_s}s`;
    case "done":
      return `run complete — ${d.rows_total} row(s), ${d.groups} matched group(s)`;
    case "heal_started":
      return `self-heal requested (${d.prompt_chars} character prompt)`;
    case "heal_previewed":
      return `fix proposed at step ${d.step}, awaiting approval`;
    case "heal_approved":
      return `approved, auto_save ${d.auto_save === true ? "on" : "off"}`;
    case "heal_promoted":
      return `healed template saved to production${d.template ? ` (${d.template})` : ""}`;
    default:
      return event.type;
  }
}
