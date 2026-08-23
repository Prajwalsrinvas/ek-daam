import { useEffect, useRef } from "react";

import type { RunState } from "../runState";
import type { Universe } from "../types";
import EventFeed from "./EventFeed";

export interface RecorderLane {
  runId: string;
  /** The cart item this run is for, or null for a single run. */
  label: string | null;
  state: RunState;
}

/** The raw event log, one click from anywhere on the page.
 *
 *  It is a drawer rather than a panel because the log is evidence, not the
 *  headline: it should be reachable at any moment and in the way of nothing. The
 *  tab says how many events are in it, so a reader knows whether it is worth
 *  opening before they open it.
 */
export default function FlightRecorder({
  lanes,
  registry,
  open,
  onOpenChange,
}: {
  lanes: RecorderLane[];
  registry: Universe[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const count = lanes.reduce((n, lane) => n + lane.state.feed.length, 0);

  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  return (
    <>
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-controls="flight-recorder"
        className="recorder-tab kicker fixed right-0 top-1/2 z-30 -translate-y-1/2 px-2 py-4"
        style={{
          background: "var(--ink-2)",
          border: "1px solid var(--hair)",
          borderRight: "none",
          color: "var(--text)",
        }}
      >
        FLIGHT RECORDER · {count} events
      </button>

      {/* A scrim rather than a modal: the page underneath stays live, but the
          drawer stops competing with it for attention, and clicking away is the
          gesture everyone already knows. */}
      <div
        aria-hidden="true"
        onClick={() => onOpenChange(false)}
        className="fixed inset-0 z-30"
        style={{
          background: "rgba(21,26,38,.35)",
          opacity: open ? 1 : 0,
          pointerEvents: open ? "auto" : "none",
          transition: "opacity 220ms ease",
        }}
      />

      <aside
        id="flight-recorder"
        aria-label="flight recorder"
        aria-hidden={!open}
        className="drawer fixed right-0 top-0 z-40 flex h-full w-[min(620px,94vw)] flex-col"
        style={{
          background: "var(--ink-2)",
          borderLeft: "1px solid var(--hair)",
          transform: open ? "translateX(0)" : "translateX(101%)",
          boxShadow: open ? "-24px 0 60px -30px rgba(21,26,38,.45)" : "none",
        }}
      >
        <header
          className="flex items-baseline justify-between gap-3 px-4 py-3"
          style={{ borderBottom: "1px solid var(--hair)" }}
        >
          <div>
            <h2 className="kicker">Flight recorder</h2>
            <p className="mt-1 text-[11px] text-[var(--text-dim)]">
              Every event the server wrote for what is on screen, in order.
            </p>
          </div>
          <button
            ref={closeButton}
            type="button"
            onClick={() => onOpenChange(false)}
            className="ctl py-1 text-[12px]"
            tabIndex={open ? 0 : -1}
          >
            Close
          </button>
        </header>

        <div className="scroll-in flex-1">
          {lanes.length === 0 && (
            <p className="px-4 py-5 text-[13px] text-[var(--text-dim)]">
              Nothing is streaming. Start a demo or a live comparison and every event lands here.
            </p>
          )}
          {lanes.map((lane) => (
            <section key={lane.runId}>
              <h3
                className="mono px-4 py-2 text-[11px] text-[var(--text-dim)]"
                style={{ borderBottom: "1px solid var(--hair)" }}
              >
                {lane.label ? `${lane.label} · ` : ""}
                {lane.runId}
                {lane.state.replay ? " · replay" : ""}
              </h3>
              <EventFeed events={lane.state.feed} registry={registry} />
            </section>
          ))}
        </div>
      </aside>
    </>
  );
}
