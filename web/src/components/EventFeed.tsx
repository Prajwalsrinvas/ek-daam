import { useEffect, useRef } from "react";

import { describe } from "../runState";
import { istClockMs } from "../time";
import type { RunEvent, Universe } from "../types";

/** The raw log, unchanged: one line per event, in the order the server wrote
 *  them, with its own timestamp, universe, type and description. It lives inside
 *  the flight recorder drawer now, and nowhere else. */
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
    // document - so every new event yanked the whole page down and away from the
    // comparison the reader was looking at.
    const el = box.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events.length]);

  return (
    <div ref={box} className="mono scroll-in max-h-[70vh] text-[11px]">
      {events.length === 0 && (
        <p className="px-3 py-4 text-[var(--text-dim)]">
          No events yet. Every observed state lands here, in order, as it happens.
        </p>
      )}
      <ul>
        {events.map((e) => (
          <li
            key={e.i}
            className="flex gap-3 px-3 py-1.5"
            style={{ borderBottom: "1px solid var(--hair)" }}
          >
            <span className="shrink-0 text-[var(--text-dim)]">{istClockMs(e.ts) ?? e.ts}</span>
            <span
              className="w-20 shrink-0 truncate"
              style={{ color: colors.get(e.universe ?? "") ?? "var(--text-dim)" }}
            >
              {e.universe ?? "run"}
            </span>
            <span className="w-36 shrink-0 truncate">{e.type}</span>
            {/* Amber, not red: the rows are fine, only the evidence capture is
                missing. Red stays reserved for a universe that actually failed. */}
            <span
              className="min-w-0"
              style={{ color: e.type === "artifact_failed" ? "var(--warn)" : "var(--text-dim)" }}
            >
              {describe(e)}
            </span>
            {e.replay && (
              <span className="ml-auto shrink-0" style={{ color: "var(--warn)" }}>
                replay
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
