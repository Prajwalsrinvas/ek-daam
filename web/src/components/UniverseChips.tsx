import type { RunState, UniverseState } from "../runState";
import type { Universe } from "../types";

const LABEL: Record<UniverseState["status"], string> = {
  idle: "idle",
  dispatched: "dispatched",
  triggered: "triggered",
  collecting: "collecting",
  rows: "parsing",
  validated: "done",
  zero_rows: "no rows",
  failed: "failed",
  timed_out: "timed out",
};

function detail(u: UniverseState): string {
  if (u.status === "collecting" && u.pagesLeft !== null) return `${u.pagesLeft} page(s) left`;
  if (u.status === "validated" && u.rowsKept !== null) return `${u.rowsKept} rows`;
  if (u.status === "zero_rows" && u.zeroReason) return u.zeroReason;
  if (u.status === "failed" && u.error) return u.error.slice(0, 60);
  if (u.status === "rows" && u.rows !== null) return `${u.rows} rows`;
  return "";
}

export default function UniverseChips({
  state,
  registry,
}: {
  state: RunState;
  registry: Universe[];
}) {
  const byId = new Map(registry.map((u) => [u.id, u]));

  if (state.order.length === 0) {
    return (
      <div className="flex flex-wrap gap-2">
        {registry.map((u) => (
          <span
            key={u.id}
            className="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-500"
          >
            <span className="mr-2 inline-block h-2 w-2 rounded-full" style={{ background: u.color }} />
            {u.display}: {u.status}
          </span>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap gap-2">
      {state.order.map((id) => {
        const u = state.universes[id];
        const meta = byId.get(id);
        const bad = u.status === "failed" || u.status === "timed_out" || u.status === "zero_rows";
        return (
          <span
            key={id}
            className={`rounded border px-3 py-2 text-sm ${
              bad ? "border-red-300 bg-red-50 text-red-800" : "border-slate-200 bg-white"
            }`}
          >
            <span
              className="mr-2 inline-block h-2 w-2 rounded-full align-middle"
              style={{ background: meta?.color ?? "#64748b" }}
            />
            <strong>{meta?.display ?? id}</strong>
            <span className="ml-2 text-slate-600">{LABEL[u.status]}</span>
            {detail(u) && <span className="ml-2 text-slate-400">· {detail(u)}</span>}
          </span>
        );
      })}
    </div>
  );
}
