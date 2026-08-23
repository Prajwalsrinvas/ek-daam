// The event fold. DESIGN.md §8 calls this THE core UI logic, so it is a pure
// function of (state, event) with no React, no fetch and no clock in it.
//
// ADDING A SERVER EVENT TYPE takes THREE edits here, not one:
//   1. the `EventType` union in types.ts
//   2. `IMPLEMENTED_EVENT_TYPES` below - it is the SSE subscription list, and a
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
  // The additional model matching layer, run-level, emitted after the
  // deterministic comparison and before `done`.
  "llm_match",
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
  /** The collector version the job was actually triggered with, as the event
   *  reported it. Read from the event rather than from the registry so a run
   *  captured against an older version still says which one it used. */
  jobVersion: string | null;
  pagesLeft: number | null;
  rows: number | null;
  rowsKept: number | null;
  rowsDropped: number | null;
  dropReasons: Record<string, number>;
  zeroReason: string | null;
  error: string | null;
  /** A page capture we could not fetch. Non-terminal - the rows still stand, so
   *  this never changes `status`. */
  artifactError: string | null;
  screenshot: { artifact: string; url: string; placeholder: boolean } | null;
  /** Bright Data's own name for the stage a job is on. A scrape job counts pages
   *  and leaves this null; a self-heal job names the stage instead. */
  stage: string | null;
  /** The self-heal cycle for THIS universe, as far as it has been observed.
   *  Null until a heal run says otherwise, which is every ordinary run. */
  heal: HealState | null;
  /** The stalled job that was canceled, and how long it was watched before the
   *  watchdog gave up on it. Non-terminal and deliberately sticky: it stays
   *  visible for the rest of the run because it is the interesting part. */
  retriggeredAfterS: number | null;
  lastEventAt: string | null;
}

/** The four observed steps of a self-heal, in order. Each one is a real event
 *  from server/heal.py; nothing here advances on a timer. */
export type HealStep = "plan" | "preview" | "approve" | "promote";

export const HEAL_STEPS: HealStep[] = ["plan", "preview", "approve", "promote"];

export interface HealState {
  step: HealStep;
  /** One line of the event's own data: the prompt size, the stage Bright Data
   *  paused at, the row count in the preview, the template that was published. */
  detail: string | null;
}

/** The model matching layer's state, folded from the `llm_match` events. */
export interface LlmState {
  status: "started" | "done" | "failed" | "skipped" | null;
  model: string | null;
  blocks: number | null;
  rowsSent: number | null;
  accepted: number | null;
  rejected: number | null;
  seconds: number | null;
  reason: string | null;
  /** The `started` event's timestamp. The receipt shows a REAL elapsed time
   *  while the model is thinking, counted from here, not from a fake bar. */
  startedAt: string | null;
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
  /** The model layer, or null on a run that never reported one. */
  llm: LlmState | null;
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
    llm: null,
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
    jobVersion: null,
    pagesLeft: null,
    rows: null,
    rowsKept: null,
    rowsDropped: null,
    dropReasons: {},
    zeroReason: null,
    error: null,
    artifactError: null,
    screenshot: null,
    stage: null,
    heal: null,
    retriggeredAfterS: null,
    lastEventAt: null,
  };
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

const EMPTY_LLM: LlmState = {
  status: null,
  model: null,
  blocks: null,
  rowsSent: null,
  accepted: null,
  rejected: null,
  seconds: null,
  reason: null,
  startedAt: null,
};

/** Forward only. Bright Data's heal job reports each step once, but a resumed
 *  stream can re-deliver an earlier one, and a strip that walked backwards would
 *  claim the repair had come undone. */
function advanceHeal(current: HealState | null, step: HealStep, detail: string): HealState {
  if (current && HEAL_STEPS.indexOf(current.step) > HEAL_STEPS.indexOf(step)) return current;
  return { step, detail };
}

/** The preview line: the stage the job paused at, plus how many rows the
 *  proposed fix actually returned. The row count is the thing a reviewer
 *  approves on, so it is counted rather than described. */
function previewDetail(d: Record<string, unknown>): string {
  const step = str(d.step);
  const preview = d.preview_result;
  const rows = Array.isArray(preview) ? preview.length : preview ? 1 : 0;
  const at = step ? `paused at ${step}` : "paused for approval";
  return `${at}, ${rows} preview row(s)`;
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
        u.jobVersion = str(d.version) ?? u.jobVersion;
      }
      break;
    case "progress":
      if (u) {
        u.status = "collecting";
        u.pagesLeft = num(d.pages_left);
        // A scrape job counts pages and sends no `step`; a self-heal job names
        // the stage instead. Kept rather than overwritten with null so the last
        // named stage survives the next page-count tick.
        u.stage = str(d.step) ?? u.stage;
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
      //
      // It IS recorded, because a universe that needed a second try is the most
      // interesting thing on the deck, and the pill it puts in that universe's
      // header has to survive every later event in the run.
      if (u) u.retriggeredAfterS = num(d.after_s);
      break;
    case "failed":
      if (u) {
        u.status = "failed";
        u.error = str(d.error);
      } else {
        // Run-level. The server emits this INSTEAD of `done`, so without
        // settling here the UI waited forever on a run that was already over -
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
    case "llm_match": {
      // Run-level and additive. Each event carries only the fields that changed,
      // so `started`'s model and block counts survive into `done`.
      const status = str(d.status) as LlmState["status"] | null;
      const before = next.llm ?? EMPTY_LLM;
      next.llm = {
        status: status ?? before.status,
        model: str(d.model) ?? before.model,
        blocks: num(d.blocks) ?? before.blocks,
        rowsSent: num(d.rows_sent) ?? before.rowsSent,
        accepted: num(d.accepted) ?? before.accepted,
        rejected: num(d.rejected) ?? before.rejected,
        seconds: num(d.seconds) ?? before.seconds,
        reason: str(d.reason) ?? before.reason,
        startedAt: status === "started" ? event.ts : before.startedAt,
      };
      break;
    }
    // The self-heal cycle. Every step is an observed one and they only ever move
    // forward, so a late-arriving earlier event cannot walk the strip backwards.
    case "heal_started":
      if (u) u.heal = advanceHeal(u.heal, "plan", `${num(d.prompt_chars) ?? 0} character prompt`);
      break;
    case "heal_previewed":
      if (u) u.heal = advanceHeal(u.heal, "preview", previewDetail(d));
      break;
    case "heal_approved":
      if (u)
        u.heal = advanceHeal(
          u.heal,
          "approve",
          `approved, auto_save ${d.auto_save === true ? "on" : "off"}`,
        );
      break;
    case "heal_promoted":
      if (u)
        u.heal = advanceHeal(
          u.heal,
          "promote",
          str(d.template) ? `published ${str(d.template)}` : "published to production",
        );
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

/** One terse human line per event. No invented adjectives - just what happened. */
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
      return `no page capture: ${d.error} (rows unaffected)`;
    case "validated": {
      const reasons = Object.entries((d.reasons as Record<string, number>) ?? {})
        .map(([k, v]) => `${k}×${v}`)
        .join(", ");
      return `${d.rows_kept} kept, ${d.rows_dropped} dropped${reasons ? ` (${reasons})` : ""}`;
    }
    case "zero_rows":
      return `no usable rows: ${d.reason}`;
    case "retriggered":
      return `collector job never started, canceled and retriggered after ${d.after_s}s`;
    case "failed":
      return event.universe ? `failed: ${d.error}` : `run failed: ${d.error}`;
    case "timed_out":
      return event.universe
        ? `timed out after ${d.after_s}s`
        : `run timed out after ${d.after_s}s`;
    case "done": {
      // `rows_total` counts the real universes. A demo universe's rows are named
      // separately because they were never eligible to be compared.
      const demo = num(d.demo_rows);
      return (
        `run complete: ${d.rows_total} row(s), ${d.groups} matched group(s)` +
        (demo ? `, ${demo} demo row(s) shown separately` : "")
      );
    }
    case "llm_match":
      // Four different sentences, because the four statuses mean four different
      // things and one of them is "we did not ask".
      switch (d.status) {
        case "started":
          return `asked ${d.model}: ${d.blocks} block(s), ${d.rows_sent} row(s) sent`;
        case "done":
          return `${d.model} suggested ${d.accepted} group(s), ${d.rejected} rejected by the guards, ${d.seconds}s`;
        case "failed":
          return `model layer failed: ${d.reason}`;
        case "skipped":
          return `model layer skipped: ${d.reason}`;
        default:
          return "model layer reported an unknown status";
      }
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
