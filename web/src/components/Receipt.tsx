import { hueInk } from "../format";
import { useMemo } from "react";

import { byPriceAscending, money, plural, unitPriceLabel } from "../format";
import type { LlmState } from "../runState";
import { secondsSince } from "../time";
import type { Comparison, LlmReport, NormalizedRow, Universe } from "../types";
import Payline from "./Payline";

/** The receipt: four shelves collapsed into one honest line per product.
 *
 *  What is on it is only ever what the run observed. The deterministic groups
 *  come first and are labelled `close`, which is the strongest word the resolver
 *  is entitled to. The model's groups come after, on dashed lines, labelled as
 *  suggestions with the model's own confidence. Nothing the model says changes a
 *  price, a name or a `close` group.
 */

function RowCard({ row, universe }: { row: NormalizedRow; universe: Universe | undefined }) {
  const unit = unitPriceLabel(row);
  return (
    <li
      className="flex items-baseline justify-between gap-3 py-1.5"
      style={{ borderTop: "1px solid var(--paper-hair)" }}
    >
      <span className="min-w-0 flex-1 truncate text-[12px]" title={row.name}>
        {row.product_url ? (
          <a
            href={row.product_url}
            target="_blank"
            rel="noreferrer noopener"
            className="paper-link"
          >
            {row.name}
          </a>
        ) : (
          row.name
        )}
      </span>
      <span className="mono shrink-0 text-[11px]" style={{ color: "var(--paper-dim)" }}>
        {unit ?? (universe ? universe.id : "")}
      </span>
      <span className="display shrink-0 text-[15px]">{money(row.price)}</span>
    </li>
  );
}

function ByUniverse({ rows, universes }: { rows: NormalizedRow[]; universes: Universe[] }) {
  const present = universes.filter((u) => rows.some((row) => row.universe === u.id));
  return (
    <div
      className="deck-grid mt-3"
      style={{ ["--cols" as string]: Math.max(present.length, 1) }}
    >
      {present.map((universe) => {
        const mine = rows.filter((row) => row.universe === universe.id).sort(byPriceAscending);
        return (
          <div key={universe.id} className="min-w-0">
            <h4 className="kicker pb-1" style={{ color: hueInk(universe.color) }}>
              {universe.display} · {plural(mine.length, "listing")}
            </h4>
            <ul>
              {mine.map((row, i) => (
                <RowCard
                  key={`${row.product_id ?? row.name}-${i}`}
                  row={row}
                  universe={universe}
                />
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

/** The model layer has TWO honest sources and they are not interchangeable.
 *
 *  The events say what is happening now, including "started", which is the only
 *  place the "asking the model..." line can come from. The stored snapshot says
 *  what happened, and it is the only source left after a reload, or on a capture
 *  whose events this browser never streamed. So the live fold wins when it has
 *  anything, and the snapshot fills in otherwise. Reading only the events made a
 *  finished run with a real model report say "no model layer ran on this
 *  capture", which was simply untrue.
 */
function reportFor(folded: LlmState | null, stored: LlmReport | null): LlmState | null {
  if (folded && folded.status !== null) return folded;
  if (!stored || stored.status === null) return folded;
  return {
    status: stored.status,
    model: stored.model,
    blocks: stored.blocks,
    rowsSent: stored.rows_sent,
    accepted: stored.accepted,
    rejected: stored.rejected,
    seconds: stored.seconds,
    reason: stored.reason,
    // The snapshot records no start moment, and the elapsed counter it feeds is
    // only ever shown while the status is "started", which the snapshot is not.
    startedAt: null,
  };
}

/** The model layer's own line, in its own words. Four statuses, four sentences,
 *  and one of them is "we did not ask". */
function modelLine(llm: LlmState | null, added: number, now: number): string {
  if (!llm || llm.status === null) return "no model layer ran on this capture";
  switch (llm.status) {
    case "started": {
      const since = secondsSince(llm.startedAt, now);
      return `asking ${llm.model ?? "the model"}...${since === null ? "" : ` ${since}s`}`;
    }
    case "done":
      // `accepted` counts every group that passed the guards, INCLUDING ones
      // the rules had already aligned; `llm_groups` is only what the model
      // ADDS, because the server drops the duplicates. Printing `accepted` as
      // the number of lines below it would promise more than the section shows.
      return (
        `asked ${llm.model ?? "the model"}, ${plural(llm.blocks ?? 0, "block")}, ` +
        `${llm.seconds ?? 0}s, ${llm.accepted ?? 0} groups passed the guards, ` +
        `${llm.rejected ?? 0} rejected, ${added} new`
      );
    case "failed":
      return `model layer failed: ${llm.reason ?? "no reason given"}`;
    case "skipped":
      return `model skipped: ${llm.reason ?? "no reason given"}`;
  }
}

export default function Receipt({
  comparison,
  registry,
  query,
  pincode,
  llm,
  landing,
  done,
  now,
}: {
  comparison: Comparison;
  registry: Universe[];
  query: string;
  pincode: string | null;
  llm: LlmState | null;
  landing: boolean;
  done: boolean;
  now: number;
}) {
  const groups = comparison.groups ?? [];
  const modelGroups = comparison.llm_groups ?? [];
  const unmatched = comparison.unmatched ?? [];
  const demoRows = comparison.demo_rows ?? [];

  // The universes the receipt has columns for: registry order, real stores only,
  // and only those this run actually produced an aligned row for. A universe
  // that returned nothing gets no empty column pretending it might have.
  const columns = useMemo(() => {
    const seen = new Set([...groups, ...modelGroups].flatMap((g) => g.universes));
    const real = registry.filter((u) => u.badge !== "chaos");
    const hit = real.filter((u) => seen.has(u.id));
    return hit.length > 0 ? hit : real;
  }, [groups, modelGroups, registry]);

  const unmatchedRows = unmatched.flatMap((g) => g.rows);
  const report = reportFor(llm, comparison.llm ?? null);
  const status = report?.status ?? null;

  if (landing) {
    return (
      <section className="paper px-6 py-10">
        <p className="kicker" style={{ color: "var(--paper-dim)" }}>
          RECEIPT
        </p>
        <p className="display mt-2 max-w-2xl text-[26px] leading-tight">
          Pick a demo above or compare something live.
        </p>
        <p className="mt-2 max-w-2xl text-[13px]" style={{ color: "var(--paper-dim)" }}>
          Four collectors open four shops at one pincode, and whatever they read off the shelves
          lands here as one line per product: every price, the site that quoted it, and the word
          for how sure the match is.
        </p>
      </section>
    );
  }

  const nothing =
    done && groups.length + unmatched.length + demoRows.length + modelGroups.length === 0;

  return (
    <section className="paper">
      <header
        className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-4 py-3"
        style={{ borderBottom: "1px solid var(--paper-hair)" }}
      >
        <span className="kicker">RECEIPT</span>
        <span className="mono text-[12px]">
          “{query || "no query"}” at {pincode ?? "no pincode"}
        </span>
        <span className="mono text-[12px]" style={{ color: "var(--paper-dim)" }}>
          · {groups.length} aligned · {unmatched.length} unmatched · {demoRows.length} demo rows
          · model: {status ?? (done ? "not run" : "not reported yet")}
        </span>
      </header>

      {nothing ? (
        <p className="px-4 py-6 text-[13px]" style={{ color: "var(--paper-dim)" }}>
          Run complete, no comparable rows. Every universe either failed the location proof,
          returned nothing usable, or produced no row that survived the validation gate.
        </p>
      ) : (
        <div className="scroll-hint">
          <div className="scroll-in px-4 pb-6" style={{ maxHeight: "62vh" }}>
            {/* The column heads. Sticky, because a receipt read halfway down still
              has to say which universe a price belongs to. */}
            <div
              className="payline-grid wide-only sticky top-0 z-10 py-2"
              style={{
                ["--ucols" as string]: columns.length,
                background: "var(--paper)",
                borderBottom: "1px solid var(--paper-hair)",
              }}
            >
              <div className="payline-head kicker" style={{ color: "var(--paper-dim)" }}>
                PRODUCT
              </div>
              {columns.map((universe) => (
                <div
                  key={universe.id}
                  className="kicker px-2"
                  style={{ color: hueInk(universe.color) }}
                >
                  {universe.display}
                </div>
              ))}
              <div className="payline-tag kicker" style={{ color: "var(--paper-dim)" }}>
                MATCH
              </div>
            </div>

            {groups.length > 0 && (
              <div style={{ ["--ucols" as string]: columns.length }}>
                {groups.map((group, i) => (
                  <Payline key={group.key} group={group} universes={columns} index={i} />
                ))}
              </div>
            )}

            {groups.length === 0 && !nothing && (
              <p className="py-4 text-[13px]" style={{ color: "var(--paper-dim)" }}>
                {done
                  ? "No rule could align a single product across two universes in this run. Everything the collectors read is under “no rule could align” below."
                  : // The run is not over. Saying nothing aligned would be a
                    // conclusion the receipt has not reached yet.
                    "Nothing aligned yet. A line appears here the moment two universes agree on the same brand, pack and product name."}
              </p>
            )}

            {/* The model layer. Always labelled, never mixed into the lines above,
              and shown even when it added nothing so the reader knows it ran. */}
            {(status !== null || modelGroups.length > 0) && (
              <section
                className="mt-5 pt-3"
                style={{ borderTop: "2px solid var(--paper-ink)" }}
              >
                <h3 className="kicker">MODEL-SUGGESTED, ON TOP OF THE RULES</h3>
                <p className="mono mt-1 text-[11px]" style={{ color: "var(--paper-dim)" }}>
                  {modelLine(report, modelGroups.length, now)}
                </p>
                <div className="mt-2" style={{ ["--ucols" as string]: columns.length }}>
                  {modelGroups.map((group, i) => (
                    <Payline
                      key={`llm-${group.key}`}
                      group={{ ...group, source: "model" }}
                      universes={columns}
                      index={i}
                    />
                  ))}
                </div>
                {modelGroups.length === 0 && status === "done" && (
                  <p className="mt-2 text-[12px]" style={{ color: "var(--paper-dim)" }}>
                    The model added nothing the rules had not already aligned.
                  </p>
                )}
              </section>
            )}
          </div>
        </div>
      )}

      {/* The two collapsed sections live OUTSIDE the paylines' scroll box on
          purpose: buried under twelve rows of an inner scroller, a reader
          would never find the ninety-five listings the rules could not
          align, which is the most honest thing on the page. */}
      <div className="px-4 pb-3">
        {unmatchedRows.length > 0 && (
          <details className="mt-5 pt-3" style={{ borderTop: "1px solid var(--paper-hair)" }}>
            <summary className="kicker cursor-pointer select-none">
              {unmatched.length} LISTINGS NO RULE COULD ALIGN
            </summary>
            <p className="mt-2 text-[12px]" style={{ color: "var(--paper-dim)" }}>
              One universe only: either nobody else listed it, or its pack size could not be
              parsed, and an unknown pack is never matched across universes. Shown, not matched.
            </p>
            <div className="scroll-in" style={{ maxHeight: "44vh" }}>
              <ByUniverse rows={unmatchedRows} universes={registry} />
            </div>
          </details>
        )}

        {demoRows.length > 0 && (
          <details className="mt-4 pt-3" style={{ borderTop: "1px solid var(--paper-hair)" }}>
            <summary className="kicker cursor-pointer select-none">
              {registry.find((u) => u.badge === "chaos")?.display ?? "Demo store"} ·{" "}
              {demoRows.length} DEMO ROWS · INVENTED PRICES, NEVER MATCHED
            </summary>
            <p className="mt-2 text-[12px]" style={{ color: "var(--paper-dim)" }}>
              A store this app serves itself, so a collector can be broken and repaired on
              demand. Its products and prices are invented.{" "}
              <a href="/chaos" target="_blank" rel="noreferrer noopener" className="paper-link">
                open the demo store
              </a>
            </p>
            <div className="scroll-in" style={{ maxHeight: "40vh" }}>
              <ByUniverse rows={demoRows} universes={registry} />
            </div>
          </details>
        )}
      </div>

      <footer
        className="px-4 py-3 text-[12px]"
        style={{
          borderTop: "1px solid var(--paper-hair)",
          color: "var(--paper-dim)",
        }}
      >
        Sahi sahi lagao: shelf price only, as each site listed it at the moment it was read. No
        delivery fees, no handling charges, no member pricing.
      </footer>
    </section>
  );
}
