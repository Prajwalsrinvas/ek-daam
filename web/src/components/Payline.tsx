import { cheapestPrice, groupLabel, money } from "../format";
import type { ComparisonGroup, NormalizedRow, Universe } from "../types";

/** One aligned group, spread across the universe columns.
 *
 *  The line under it is the payline itself: SOLID when the deterministic
 *  resolver aligned the group, DASHED when the model suggested it. That is the
 *  only place the difference is drawn, and it is never the only place it is
 *  said - the label on the right spells it out in words.
 */

function Cell({
  rows,
  best,
  tied,
  universe,
}: {
  rows: NormalizedRow[];
  /** The cheapest price in this group, or null when no cell has a price. */
  best: number | null;
  /** More than one universe quoted that same cheapest price. Calling all three
   *  of them BEST would read as three winners, so they are called what they
   *  are. */
  tied: boolean;
  universe: Universe;
}) {
  if (rows.length === 0) {
    return (
      <div className="mono px-2 py-1.5 text-[13px]" style={{ color: "#b6ae9e" }}>
        <span aria-label={`${universe.display} did not list this`}>-</span>
      </div>
    );
  }

  // A universe can list the same pack twice. The cell shows the cheapest of them
  // and says how many others there were, rather than picking one silently.
  const sorted = [...rows].sort((a, b) => (a.price ?? Infinity) - (b.price ?? Infinity));
  const row = sorted[0];
  const lit = best !== null && row.price === best;

  return (
    <div
      className="min-w-0 px-2 py-1.5"
      style={
        lit
          ? { background: "var(--paper-ink)", color: "var(--paper)" }
          : { background: "transparent" }
      }
    >
      <div className="kicker only-narrow mb-0.5" style={{ color: lit ? "var(--paper)" : universe.color }}>
        {universe.display}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="display text-[19px]">{money(row.price)}</span>
        {lit && (
          <span className="kicker" style={{ color: "var(--paper)" }}>
            {tied ? "TIED" : "BEST"}
          </span>
        )}
      </div>
      <div
        className="truncate text-[11px] leading-snug"
        title={row.name}
        style={{ color: lit ? "rgba(246,243,236,.78)" : "var(--paper-dim)" }}
      >
        {row.product_url ? (
          <a
            href={row.product_url}
            target="_blank"
            rel="noreferrer noopener"
            className="paper-link"
            style={lit ? { color: "var(--paper)", textDecorationColor: "rgba(246,243,236,.5)" } : undefined}
          >
            {row.name}
          </a>
        ) : (
          row.name
        )}
      </div>
      <div
        className="mono text-[11px]"
        style={{ color: lit ? "rgba(246,243,236,.7)" : "var(--paper-dim)" }}
      >
        {row.eta_min !== null ? `${row.eta_min} min` : "no eta listed"}
        {sorted.length > 1 ? ` · +${sorted.length - 1} more here` : ""}
      </div>
    </div>
  );
}

export default function Payline({
  group,
  universes,
  index,
}: {
  group: ComparisonGroup;
  /** The universe columns, in registry order. Every payline uses the same set so
   *  the receipt reads down a column as well as across a line. */
  universes: Universe[];
  index: number;
}) {
  const model = (group.source ?? "rules") === "model";
  const best = cheapestPrice(group.rows);
  // Counted per UNIVERSE, not per row: one universe listing the same pack twice
  // at the same price is not a tie between shops.
  const atBest = new Set(
    group.rows.filter((row) => row.price !== null && row.price === best).map((row) => row.universe),
  );
  const tied = atBest.size > 1;

  return (
    <div
      className={`payline payline-grid py-2.5 ${model ? "payline--model" : ""}`}
      style={{ ["--i" as string]: Math.min(index, 12) }}
    >
      <div className="payline-head min-w-0">
        <div className="mono text-[12px] leading-snug">{groupLabel(group)}</div>
        <div className="mono text-[11px]" style={{ color: "var(--paper-dim)" }}>
          {group.universes.length} of {universes.length} universes
        </div>
      </div>
      {universes.map((universe) => (
        <Cell
          key={universe.id}
          universe={universe}
          rows={group.rows.filter((row) => row.universe === universe.id)}
          best={best}
          tied={tied}
        />
      ))}
      <div className="payline-tag mono text-[11px]" style={{ color: "var(--paper-dim)" }}>
        {model ? `model-suggested · ${group.confidence}` : group.confidence}
      </div>
    </div>
  );
}
