import { plural } from "../format";
import type { DemoEntry } from "../types";

/** What each kind of capture is, in a chip. The number is the number of runs it
 *  really drives, so a card can never claim more than it replays. */
function kindLabel(entry: DemoEntry): string {
  const n = entry.run_ids.length;
  if (entry.kind === "cart") return `cart · ${plural(entry.items?.length ?? n, "item")}`;
  if (entry.kind === "story") return `story · ${plural(entry.chapters?.length ?? n, "chapter")}`;
  return "1 product";
}

/** The captures anyone may replay without running anything. A card starts the
 *  replay (or replays, for a cart) and the REPLAY banner comes up with it. */
export default function DemoStrip({
  entries,
  onPlay,
  busy,
  playingId,
}: {
  entries: DemoEntry[];
  onPlay: (entry: DemoEntry) => void;
  busy: boolean;
  playingId: string | null;
}) {
  if (entries.length === 0) {
    return (
      <p className="panel px-4 py-3 text-[13px] text-[var(--text-dim)]">
        No demo captures are configured on this server. Switch to “Try it live” to start a run of
        your own, or replay one of your own runs below.
      </p>
    );
  }

  return (
    <div className="flex gap-3 overflow-x-auto pb-1">
      {entries.map((entry) => {
        const playing = entry.id === playingId;
        return (
          <button
            key={entry.id}
            type="button"
            onClick={() => onPlay(entry)}
            disabled={busy}
            className="panel min-w-0 shrink-0 basis-[300px] px-3.5 py-3 text-left"
            style={{
              cursor: busy ? "not-allowed" : "pointer",
              borderColor: playing ? "var(--warn)" : "var(--hair)",
              // The card that is playing stays legible; the ones that cannot be
              // started right now are the ones that dim.
              opacity: busy && !playing ? 0.4 : 1,
            }}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="kicker text-[var(--text-dim)]">{kindLabel(entry)}</span>
              {playing && (
                <span className="kicker" style={{ color: "var(--warn)" }}>
                  REPLAYING
                </span>
              )}
            </div>
            <div className="display mt-1.5 text-[17px]">{entry.title}</div>
            <p className="mt-1 text-[12px] leading-snug text-[var(--text-dim)]">{entry.note}</p>
          </button>
        );
      })}
    </div>
  );
}
