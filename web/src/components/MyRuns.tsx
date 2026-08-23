import { istClock } from "../time";
import type { RunMeta } from "../types";

/** The visitor's own completed captures, newest first.
 *
 *  "My" is anonymous scoping, NOT an account: the server hands every browser one
 *  opaque cookie and a run remembers only its hash, so this list is whatever
 *  this browser has run. Clearing cookies makes a new identity and empties it.
 *  Nothing here is private in the security sense and the panel says so.
 */
export default function MyRuns({
  runs,
  onReplay,
  busy,
  showingRunId,
}: {
  runs: RunMeta[];
  onReplay: (runId: string) => void;
  busy: boolean;
  showingRunId: string | null;
}) {
  const done = runs.filter((run) => run.status === "done");
  if (done.length === 0) return null;

  return (
    <section className="rounded border border-slate-200 bg-white">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-200 px-4 py-2">
        <h2 className="text-sm font-semibold text-slate-800">My runs</h2>
        <p className="text-xs text-slate-500">
          This browser only. Runs are scoped by an anonymous cookie, not an account. Clearing
          cookies starts a new one.
        </p>
      </header>
      <ul className="divide-y divide-slate-100">
        {done.map((run) => (
          <li key={run.run_id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm">
            <span className="min-w-0 flex-1">
              <span className="font-medium text-slate-800">{run.query}</span>{" "}
              <span className="text-slate-500">· {run.pincode || "no pincode"}</span>{" "}
              <span className="text-slate-500">· {istClock(run.created_at) ?? run.created_at}</span>
              {run.replay && (
                <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-900">
                  replay
                </span>
              )}
              {run.run_id === showingRunId && (
                <span className="ml-2 text-xs text-slate-400">showing</span>
              )}
            </span>
            <code className="text-xs text-slate-400">{run.run_id}</code>
            {/* A replay of a replay is refused by the server, so it is not
                offered: the original capture is the one to re-stream. */}
            {!run.replay && (
              <button
                type="button"
                onClick={() => onReplay(run.run_id)}
                disabled={busy}
                className="rounded border border-slate-300 px-3 py-1 text-xs disabled:opacity-40"
              >
                Replay
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
