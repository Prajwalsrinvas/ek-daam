import { useEffect, useRef } from "react";

import { describe } from "../runState";
import { istClockMs } from "../time";
import type { RunEvent, Universe } from "../types";

export default function EventFeed({
  events,
  registry,
}: {
  events: RunEvent[];
  registry: Universe[];
}) {
  const colors = new Map(registry.map((u) => [u.id, u.color]));
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Scroll THIS container, not the page. `scrollIntoView` on a sentinel walks
    // up to the nearest scrollable ancestor, which on a short viewport is the
    // document — so every new event yanked the whole page down and away from the
    // comparison table the reader was looking at.
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  return (
    <div
      ref={box}
      className="h-96 overflow-y-auto rounded border border-slate-200 bg-white font-mono text-xs"
    >
      {events.length === 0 && (
        <p className="p-4 text-slate-400">No events yet. Start a run to watch collectors race.</p>
      )}
      <ul>
        {events.map((e) => (
          <li key={e.i} className="flex gap-3 border-b border-slate-100 px-3 py-1.5 last:border-0">
            <span className="shrink-0 text-slate-400">{istClockMs(e.ts) ?? e.ts}</span>
            <span className="w-24 shrink-0 truncate" style={{ color: colors.get(e.universe ?? "") ?? "#475569" }}>
              {e.universe ?? "run"}
            </span>
            <span className="w-40 shrink-0 truncate text-slate-700">{e.type}</span>
            {/* Amber, not red: the rows are fine, only the evidence capture is
                missing. Red stays reserved for a universe that actually failed. */}
            <span className={e.type === "artifact_failed" ? "text-amber-700" : "text-slate-500"}>
              {describe(e)}
            </span>
            {e.replay && <span className="ml-auto shrink-0 text-amber-600">replay</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
