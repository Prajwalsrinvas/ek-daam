import { useMemo } from "react";

import type { RunState } from "../runState";
import type { Comparison, RunEvent, Universe } from "../types";
import PortalColumn from "./PortalColumn";

/** One column per universe, in registry order - the same order the receipt's
 *  paylines use, so a price always sits under the universe that quoted it. */
export default function PortalDeck({
  registry,
  state,
  comparison,
  landing,
  now,
}: {
  registry: Universe[];
  state: RunState;
  comparison: Comparison;
  landing: boolean;
  now: number;
}) {
  // The last event per universe, for the column footers. Taken from the feed
  // rather than tracked in the fold: it is a display convenience, not state.
  const latest = useMemo(() => {
    const out: Record<string, RunEvent> = {};
    state.feed.forEach((event) => {
      if (event.universe) out[event.universe] = event;
    });
    return out;
  }, [state.feed]);

  // WHICH CLOCK the elapsed seconds are read against.
  //
  // A replay re-streams events carrying their ORIGINAL timestamps, so `now`
  // minus a replayed event's `ts` is the age of the capture, not how long the
  // job has been waiting: a job triggered two seconds ago read "5073s". The
  // honest duration during a replay is the one that really elapsed inside the
  // capture, which is the gap between this universe's last event and the newest
  // event in the run - a real interval between two real timestamps, and it
  // advances as the replay advances.
  const latestTs = state.feed.at(-1)?.ts;
  const parsedLatest = latestTs ? Date.parse(latestTs) : Number.NaN;
  const clock = state.replay && !Number.isNaN(parsedLatest) ? parsedLatest : now;

  return (
    <div className="deck-grid" style={{ ["--cols" as string]: registry.length }}>
      {registry.map((universe) => (
        <PortalColumn
          key={universe.id}
          universe={universe}
          state={state.universes[universe.id]}
          comparison={comparison}
          query={state.query ?? ""}
          pincode={state.pincode}
          latestEvent={latest[universe.id]}
          landing={landing}
          now={clock}
        />
      ))}
    </div>
  );
}
