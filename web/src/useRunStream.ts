// One run's live stream, and N of them at once for a cart.
//
// A cart is not a new kind of thing on the server: it is N ordinary runs started
// together, with no cart-level event stream. So the UI needs N subscriptions,
// and React hooks cannot be called in a loop - hence one hook that owns a MAP of
// streams, with `useRunStream` as the single-run convenience on top of it.
//
// Everything that was in App.tsx's `subscribe` lives here, unchanged in
// behaviour: the explicit per-type SSE subscription (the server names every
// frame and a named frame never reaches `onmessage`), the out-of-order guard on
// the comparison fetches, and the "ask the server what happened" recovery when
// the browser reconnects against a run that is already over.

import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";

import { eventsUrl, getRun } from "./api";
import { emptyRunState, foldEvent, IMPLEMENTED_EVENT_TYPES, type RunState } from "./runState";
import type { Comparison, RunEvent } from "./types";

export const EMPTY_COMPARISON: Comparison = {
  groups: [],
  unmatched: [],
  row_count: 0,
  universe_count: 0,
  demo_rows: [],
  llm_groups: [],
  llm: null,
};

export interface RunLane {
  runId: string;
  state: RunState;
  comparison: Comparison;
  /** Shown, never swallowed: a comparison that failed to load must not read as
   *  a run that produced nothing. */
  comparisonError: string | null;
}

type Lanes = Record<string, RunLane>;

type Action =
  | { kind: "open"; runIds: string[] }
  | { kind: "event"; runId: string; event: RunEvent }
  | { kind: "comparison"; runId: string; comparison: Comparison }
  | { kind: "error"; runId: string; message: string };

function blankLane(runId: string): RunLane {
  return {
    runId,
    state: emptyRunState(),
    comparison: EMPTY_COMPARISON,
    comparisonError: null,
  };
}

function reducer(lanes: Lanes, action: Action): Lanes {
  if (action.kind === "open") {
    // Lanes for runs we are still watching are KEPT, so adding a run to the set
    // does not throw away what the others have already folded. Anything no
    // longer in the set is dropped with its state.
    const next: Lanes = {};
    action.runIds.forEach((id) => {
      next[id] = lanes[id] ?? blankLane(id);
    });
    return next;
  }

  const lane = lanes[action.runId];
  if (!lane) return lanes; // a late frame from a stream we stopped watching

  switch (action.kind) {
    case "event": {
      const state = foldEvent(lane.state, action.event);
      if (state === lane.state) return lanes; // already folded: nothing changed
      return { ...lanes, [action.runId]: { ...lane, state } };
    }
    case "comparison":
      return {
        ...lanes,
        [action.runId]: { ...lane, comparison: action.comparison, comparisonError: null },
      };
    case "error":
      return { ...lanes, [action.runId]: { ...lane, comparisonError: action.message } };
  }
}

/** Run-level (`universe: null`) events that end a run. `done` is the happy one;
 *  the other two arrive INSTEAD of it. */
const RUN_ENDING = new Set(["done", "failed", "timed_out"]);

/** Events after which the stored comparison is worth re-reading. `llm_match` is
 *  in the list because the model layer's groups land in the snapshot, not in the
 *  event. */
const REFETCH_AFTER = new Set(["validated", "llm_match", "done"]);

export function useRunStreams(runIds: string[]): Lanes {
  const [lanes, dispatch] = useReducer(reducer, {});
  const sources = useRef<Map<string, EventSource>>(new Map());
  // How many comparison fetches have been issued per run. A run emits
  // `validated` per universe and then `done`, so several are in flight at once
  // and they do NOT come back in order: without this a slow early response
  // could overwrite a later, fuller one.
  const fetchSeq = useRef<Map<string, number>>(new Map());
  const watching = useRef<Set<string>>(new Set());

  const key = runIds.join("|");

  const refreshComparison = useCallback((runId: string) => {
    const seq = (fetchSeq.current.get(runId) ?? 0) + 1;
    fetchSeq.current.set(runId, seq);
    getRun(runId)
      .then((snapshot) => {
        if (!watching.current.has(runId) || seq !== fetchSeq.current.get(runId)) return;
        dispatch({ kind: "comparison", runId, comparison: snapshot.comparison });
      })
      .catch((e: Error) => {
        if (!watching.current.has(runId)) return;
        dispatch({ kind: "error", runId, message: `could not load the comparison: ${e.message}` });
      });
  }, []);

  useEffect(() => {
    const ids = key.length > 0 ? key.split("|") : [];
    watching.current = new Set(ids);
    dispatch({ kind: "open", runIds: ids });

    ids.forEach((runId) => {
      fetchSeq.current.set(runId, 0);

      // Native EventSource resends Last-Event-ID on reconnect, which is
      // precisely the resume the server implements.
      const es = new EventSource(eventsUrl(runId));
      sources.current.set(runId, es);

      const handler = (message: MessageEvent) => {
        const event = JSON.parse(message.data) as RunEvent;
        dispatch({ kind: "event", runId, event });
        if (REFETCH_AFTER.has(event.type)) refreshComparison(runId);
        // A run-level `failed` or `timed_out` arrives INSTEAD of `done`. The run
        // is over either way and no further event is coming, so the stream is
        // closed here or the browser reconnects against it forever.
        if (event.universe === null && RUN_ENDING.has(event.type)) es.close();
      };

      // The server names every frame (`event: rows`), and named frames never
      // reach `onmessage` - each type has to be subscribed explicitly.
      IMPLEMENTED_EVENT_TYPES.forEach((type) =>
        es.addEventListener(type, handler as EventListener),
      );
      es.onmessage = handler;
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) return;
        // Still reconnecting. If the run has actually finished there is nothing
        // to reconnect to and the browser would retry forever, so ask the server
        // what happened, fold whatever was missed, and stop.
        getRun(runId)
          .then((snapshot) => {
            if (!watching.current.has(runId)) return;
            if (snapshot.meta.status === "running") return; // a real blip; let it retry
            snapshot.events.forEach((event) => dispatch({ kind: "event", runId, event }));
            dispatch({ kind: "comparison", runId, comparison: snapshot.comparison });
            es.close();
          })
          .catch(() => undefined);
      };
    });

    const opened = sources.current;
    return () => {
      opened.forEach((es) => es.close());
      opened.clear();
    };
  }, [key, refreshComparison]);

  return lanes;
}

/** One run. Null until there is a run to watch. */
export function useRunStream(runId: string | null): RunLane | null {
  const ids = useMemo(() => (runId ? [runId] : []), [runId]);
  const lanes = useRunStreams(ids);
  return runId ? (lanes[runId] ?? null) : null;
}
