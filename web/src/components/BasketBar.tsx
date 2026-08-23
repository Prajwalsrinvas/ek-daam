import { hueInk } from "../format";
import { useMemo } from "react";

import { cheapestPrice, money } from "../format";
import type { Comparison, Universe } from "../types";

export interface BasketItem {
  label: string;
  comparison: Comparison;
}

interface Column {
  universe: Universe;
  /** The sum of the cheapest ALIGNED price for each item this universe priced. */
  total: number;
  priced: number;
}

/** What the whole cart costs, per universe.
 *
 *  The one number this bar must never print is a total that quietly leaves an
 *  item out. A universe that priced three of four items shows its three-item sum
 *  AND says it is three of four, and only a universe that priced everything is
 *  eligible for BEST BASKET.
 *
 *  Only rules-aligned rows are counted. The model's suggestions are labelled
 *  suggestions and never move a price into a total.
 */
function totals(items: BasketItem[], universes: Universe[]): Column[] {
  return universes.map((universe) => {
    let total = 0;
    let priced = 0;
    items.forEach((item) => {
      const rows = (item.comparison.groups ?? [])
        .flatMap((group) => group.rows)
        .filter((row) => row.universe === universe.id);
      const price = cheapestPrice(rows);
      if (price !== null) {
        total += price;
        priced += 1;
      }
    });
    return { universe, total, priced };
  });
}

export default function BasketBar({
  items,
  registry,
}: {
  items: BasketItem[];
  registry: Universe[];
}) {
  // Real stores only. The demo universe's prices are invented, so a basket total
  // that included them would be a number about nothing.
  const real = useMemo(() => registry.filter((u) => u.badge !== "chaos"), [registry]);
  const columns = useMemo(() => totals(items, real), [items, real]);

  const complete = columns.filter((c) => c.priced === items.length && items.length > 0);
  const best = complete.length > 0 ? Math.min(...complete.map((c) => c.total)) : null;

  return (
    <div
      className="panel sticky top-0 z-20 px-4 py-3"
      style={{ background: "var(--ink-2)" }}
      aria-label="basket total per universe"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="kicker">
          Basket · {items.length} product{items.length === 1 ? "" : "s"}
        </h2>
        <p className="text-[11px] text-[var(--text-dim)]">
          Cheapest aligned price per product, summed. Shelf prices only, real stores only.
        </p>
      </div>
      <div
        className="deck-grid mt-2.5"
        style={{ ["--cols" as string]: Math.max(real.length, 1) }}
      >
        {columns.map(({ universe, total, priced }) => {
          const full = priced === items.length && items.length > 0;
          const isBest = full && best !== null && total === best;
          return (
            <div
              key={universe.id}
              className="min-w-0 px-2 py-1"
              style={{ borderLeft: `2px solid ${universe.color}` }}
            >
              <div className="kicker truncate" style={{ color: hueInk(universe.color) }}>
                {universe.display}
              </div>
              <div className="mt-0.5 flex items-baseline gap-2">
                <span className="display text-[22px]">{priced > 0 ? money(total) : "-"}</span>
                {isBest && (
                  <span
                    className="kicker px-1"
                    style={{ border: "1px solid var(--ok)", color: "var(--ok)" }}
                  >
                    BEST BASKET
                  </span>
                )}
              </div>
              <div
                className="mono text-[11px]"
                style={{ color: full ? "var(--text-dim)" : "var(--warn)" }}
              >
                {priced} of {items.length} items priced
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
