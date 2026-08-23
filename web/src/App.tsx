import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { eventsUrl, getRun, getUniverses, startReplay, startRun } from "./api";
import ComparisonTable from "./components/ComparisonTable";
import EventFeed from "./components/EventFeed";
import Screenshots from "./components/Screenshots";
import UniverseChips from "./components/UniverseChips";
import {
  emptyRunState,
  foldEvent,
  IMPLEMENTED_EVENT_TYPES,
  type RunState,
} from "./runState";
import type { Comparison, RunEvent, Universe } from "./types";

type Action = { kind: "reset" } | { kind: "event"; event: RunEvent };

function reducer(state: RunState, action: Action): RunState {
  if (action.kind === "reset") return emptyRunState();
  return foldEvent(state, action.event);
}

const EMPTY_COMPARISON: Comparison = {
  groups: [],
  unmatched: [],
  row_count: 0,
  universe_count: 0,
};

const PINCODE_LENGTH = 6;

/** Run-level (`universe: null`) events that end a run. `done` is the happy one;
 *  the other two arrive INSTEAD of it. */
const RUN_ENDING = new Set(["done", "failed", "timed_out"]);

export default function App() {
  const [state, dispatch] = useReducer(reducer, undefined, emptyRunState);
  const [registry, setRegistry] = useState<Universe[]>([]);
  const [serverMode, setServerMode] = useState<string>("");
  const [queryAllowlist, setQueryAllowlist] = useState<string[]>([]);
  const [query, setQuery] = useState("amul butter");
  const [pincode, setPincode] = useState("");
  const [comparison, setComparison] = useState<Comparison>(EMPTY_COMPARISON);
  const [error, setError] = useState<string | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const source = useRef<EventSource | null>(null);

  // Which run the UI is currently showing, and how many comparison fetches have
  // been issued for it. A run emits `validated` per universe and then `done`, so
  // several fetches are in flight at once and they do NOT come back in order.
  // Without both guards a slow early response could overwrite a later, fuller
  // one — or land on the next run entirely and show the previous run's prices.
  const showing = useRef<string | null>(null);
  const fetchSeq = useRef(0);

  useEffect(() => {
    getUniverses()
      .then((data) => {
        setRegistry(data.universes);
        setServerMode(data.mode);
        setQueryAllowlist(data.query_allowlist);
      })
      .catch((e: Error) => setError(e.message));
    return () => source.current?.close();
  }, []);

  const refreshComparison = useCallback((runId: string) => {
    const seq = ++fetchSeq.current;
    getRun(runId)
      .then((snapshot) => {
        if (showing.current !== runId || seq !== fetchSeq.current) return;
        setComparison(snapshot.comparison);
        setComparisonError(null);
      })
      .catch((e: Error) => {
        if (showing.current !== runId) return;
        // Swallowing this left the table saying "no comparison yet" for a run
        // that had really produced one — a silent wrong answer.
        setComparisonError(`could not load the comparison — ${e.message}`);
      });
  }, []);

  const subscribe = useCallback(
    (runId: string) => {
      source.current?.close();
      dispatch({ kind: "reset" });
      setComparison(EMPTY_COMPARISON);
      setComparisonError(null);
      showing.current = runId;
      fetchSeq.current = 0;

      // Native EventSource resends Last-Event-ID on reconnect, which is precisely
      // the resume the server implements.
      const es = new EventSource(eventsUrl(runId));
      source.current = es;

      const handler = (message: MessageEvent) => {
        const event = JSON.parse(message.data) as RunEvent;
        dispatch({ kind: "event", event });
        if (event.type === "validated" || event.type === "done") refreshComparison(runId);
        // A run-level `failed` or `timed_out` arrives INSTEAD of `done`. The run
        // is over either way and no further event is coming, so the stream is
        // closed here or the browser reconnects against it forever.
        if (event.universe === null && RUN_ENDING.has(event.type)) es.close();
      };

      // The server names every frame (`event: rows`), and named frames never
      // reach `onmessage` — each type has to be subscribed explicitly.
      IMPLEMENTED_EVENT_TYPES.forEach((type) =>
        es.addEventListener(type, handler as EventListener),
      );
      es.onmessage = handler;
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          source.current = null;
          return;
        }
        // Still reconnecting. If the run has actually finished there is nothing
        // to reconnect to and the browser would retry forever, so ask the server
        // what happened, fold whatever was missed, and stop.
        getRun(runId)
          .then((snapshot) => {
            if (showing.current !== runId) return;
            if (snapshot.meta.status === "running") return; // a real blip; let it retry
            snapshot.events.forEach((event) => dispatch({ kind: "event", event }));
            setComparison(snapshot.comparison);
            es.close();
            source.current = null;
          })
          .catch(() => undefined);
      };
    },
    [refreshComparison],
  );

  const inFlight = Boolean(state.runId) && !state.done;
  const pincodeReady = pincode.length === PINCODE_LENGTH;

  async function onSearch(e: React.FormEvent) {
    e.preventDefault();
    if (starting || inFlight || !pincodeReady) return;
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startRun(query, pincode);
      subscribe(run_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  }

  async function onReplay() {
    if (!state.runId || starting) return;
    setError(null);
    setStarting(true);
    try {
      const { run_id } = await startReplay(state.runId);
      subscribe(run_id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  }

  const isMock = (state.mode ?? serverMode) === "mock";
  const nothingToCompare =
    state.done && !comparisonError && comparison.groups.length + comparison.unmatched.length === 0;

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold">EkDaam</h1>
        <p className="text-sm text-slate-600">
          Ekdam best price — one product, three universes, one pincode. Collectors race
          live; every observed state becomes an event. Shelf prices only, sahi sahi.
        </p>
      </header>

      {state.replay && (
        <div className="rounded border-2 border-amber-400 bg-amber-50 px-4 py-2 font-semibold text-amber-900">
          REPLAY — these events were captured earlier and are being re-streamed. Nothing here is live.
        </div>
      )}
      {isMock && (
        <div className="rounded border border-slate-300 bg-slate-100 px-4 py-2 text-sm text-slate-700">
          <strong>MOCK</strong> — BD_MODE=mock. Rows come from a committed fixture, not from a live
          collector run, and only one universe has one — so a mock run has nothing to compare.
        </div>
      )}

      <form onSubmit={onSearch} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-600">Product</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-72 rounded border border-slate-300 px-3 py-2"
            maxLength={60}
            placeholder="amul butter"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-slate-600">Pincode</span>
          <input
            value={pincode}
            onChange={(e) => setPincode(e.target.value.replace(/\D/g, ""))}
            className="w-28 rounded border border-slate-300 px-3 py-2"
            inputMode="numeric"
            maxLength={PINCODE_LENGTH}
            placeholder="6 digits"
          />
        </label>
        <button
          type="submit"
          // Disabled while a run is in flight: the server allows one at a time
          // and answers a second with a 429, and saying so with the button beats
          // saying it with an error message.
          disabled={starting || inFlight || !pincodeReady}
          title={
            inFlight
              ? "a run is already in flight"
              : !pincodeReady
                ? `a pincode is ${PINCODE_LENGTH} digits`
                : undefined
          }
          className="rounded bg-slate-900 px-4 py-2 text-white disabled:opacity-40"
        >
          {starting ? "starting…" : inFlight ? "running…" : "Run"}
        </button>
        {/* Only a run that actually completed: the server refuses to re-stream a
            partial capture, so offering the button for a failed run would just
            produce a 400. */}
        {state.runId && state.done && !state.error && !state.replay && (
          <button
            type="button"
            onClick={onReplay}
            disabled={starting}
            className="rounded border border-slate-300 px-4 py-2 disabled:opacity-40"
          >
            Replay this run
          </button>
        )}
        {queryAllowlist.length > 0 && (
          <span className="text-xs text-slate-500">allowed: {queryAllowlist.join(", ")}</span>
        )}
      </form>

      {error && (
        <p className="rounded border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800">{error}</p>
      )}
      {state.error && (
        <p className="rounded border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800">
          The run stopped — {state.error}
        </p>
      )}

      {state.runId && (
        <p className="text-xs text-slate-500">
          run <code>{state.runId}</code> · “{state.query}” ·{" "}
          {state.areaLabel && state.areaLabel !== state.pincode
            ? `${state.areaLabel} (${state.pincode})`
            : state.pincode}{" "}
          · {state.done ? (state.error ? "stopped" : "complete") : "in flight"}
        </p>
      )}

      <UniverseChips state={state} registry={registry} />
      <EventFeed events={state.feed} registry={registry} />
      <Screenshots state={state} registry={registry} />

      {comparisonError ? (
        <p className="rounded border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-800">
          {comparisonError}
        </p>
      ) : nothingToCompare ? (
        <p className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
          Run complete — no comparable rows. Every universe either failed the location proof,
          returned nothing usable, or produced no row that survived the validation gate.
        </p>
      ) : (
        <ComparisonTable comparison={comparison} registry={registry} />
      )}
    </div>
  );
}
