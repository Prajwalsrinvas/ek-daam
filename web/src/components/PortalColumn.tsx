import { byPriceAscending, fillSearchUrl, money, plural, siteName, unitPriceLabel } from "../format";
import type { HealState, UniverseState } from "../runState";
import { HEAL_STEPS } from "../runState";
import { istClock, secondsSince } from "../time";
import type { Comparison, NormalizedRow, RunEvent, Universe } from "../types";
import Aperture, { type ApertureTone } from "./Aperture";

/** What this universe is doing, said in words. The aperture beside it is the
 *  same fact drawn; this is the one that has to be right. */
interface Reading {
  word: string;
  tone: ApertureTone;
  fact: string;
}

/** Every state word is a state the SERVER reported. There is no word here for
 *  "probably nearly done". */
function read(u: UniverseState | undefined, pincode: string | null, now: number): Reading {
  if (!u) return { word: "WAITING", tone: "dim", fact: "no job yet" };

  const since = secondsSince(u.lastEventAt, now);
  const elapsed = since === null ? "" : `${since}s`;

  switch (u.status) {
    case "idle":
      return { word: "WAITING", tone: "dim", fact: "no job yet" };
    case "dispatched":
      return {
        word: "TRIGGERED",
        tone: "hue",
        fact: `waiting for Bright Data to start the job${elapsed ? `, ${elapsed}` : ""}`,
      };
    case "triggered":
      return {
        word: "TRIGGERED",
        tone: "hue",
        fact: `job ${u.jobId ?? "pending"}, version ${u.jobVersion ?? "unknown"}${
          elapsed ? `, ${elapsed}` : ""
        }`,
      };
    case "collecting":
      return {
        word: "COLLECTING",
        tone: "hue",
        fact:
          u.pagesLeft !== null
            ? plural(u.pagesLeft, "page") + " left"
            : u.stage
              ? `stage ${u.stage}`
              : "reading the shelf",
      };
    case "rows":
      return { word: "PARSING", tone: "hue", fact: plural(u.rows ?? 0, "row") };
    case "validated":
      return {
        word: pincode ? `PROVED ${pincode}` : "PROVED",
        tone: "ok",
        fact: `${u.rowsKept ?? 0} rows kept, ${u.rowsDropped ?? 0} dropped`,
      };
    case "zero_rows":
      return {
        // `broken` is the one zero-rows reason that means something went wrong.
        // Out of stock or unserviceable is an answer, not a fault.
        word: "NO ROWS",
        tone: u.zeroReason === "broken" ? "bad" : "dim",
        fact: u.zeroReason ?? "no reason given",
      };
    case "failed":
      return { word: "FAILED", tone: "bad", fact: u.error ?? "no error text" };
    case "timed_out":
      return { word: "TIMED OUT", tone: "warn", fact: "budget spent, the job was canceled" };
  }
}

const TONE_COLOR: Record<ApertureTone, string> = {
  hue: "var(--hue)",
  ok: "var(--ok)",
  bad: "var(--bad)",
  warn: "var(--warn)",
  dim: "var(--text-dim)",
};

const HEAL_LABEL: Record<(typeof HEAL_STEPS)[number], string> = {
  plan: "PLAN",
  preview: "PREVIEW",
  approve: "APPROVE",
  promote: "PROMOTE",
};

/** The self-heal cycle, as four observed steps. The lit one is where Bright Data
 *  says the job is now; the ones behind it really happened. Nothing here moves
 *  on a timer. */
function HealStrip({ heal }: { heal: HealState }) {
  const at = HEAL_STEPS.indexOf(heal.step);
  return (
    <div className="mt-3 space-y-1.5">
      <div className="flex items-center gap-1">
        {HEAL_STEPS.map((step, i) => {
          const done = i < at;
          const current = i === at;
          return (
            <div key={step} className="flex min-w-0 flex-1 items-center gap-1">
              <span
                className="kicker truncate"
                style={{
                  color: current ? "var(--text)" : done ? "var(--ok)" : "var(--text-dim)",
                  fontWeight: current ? 700 : 400,
                }}
              >
                {HEAL_LABEL[step]}
              </span>
              {i < HEAL_STEPS.length - 1 && (
                <span aria-hidden="true" style={{ color: "var(--text-dim)" }}>
                  ›
                </span>
              )}
            </div>
          );
        })}
      </div>
      {heal.detail && <p className="mono text-[11px] text-[var(--text-dim)]">{heal.detail}</p>}
    </div>
  );
}

function Thumb({ row }: { row: NormalizedRow }) {
  if (!row.image_url) {
    // A missing image is normal - not every collector returns one - so the slot
    // keeps its size and stays blank rather than showing a broken-image icon.
    return (
      <div
        className="h-10 w-10 shrink-0 border"
        style={{ borderColor: "var(--hair)", background: "rgba(230,233,242,.04)" }}
      />
    );
  }
  return (
    <img
      src={row.image_url}
      alt=""
      loading="lazy"
      className="h-10 w-10 shrink-0 border object-cover"
      style={{ borderColor: "var(--hair)", background: "#fff" }}
    />
  );
}

function ShelfCard({ row, index }: { row: NormalizedRow; index: number }) {
  const unit = unitPriceLabel(row);
  return (
    <li
      className="shelf-row flex gap-2.5 px-3 py-2.5"
      style={{ borderTop: "1px solid var(--hair)", ["--i" as string]: index }}
    >
      <Thumb row={row} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          {row.product_url ? (
            <a
              href={row.product_url}
              target="_blank"
              rel="noreferrer noopener"
              className="link min-w-0 flex-1 truncate text-[13px] text-[var(--text)]"
              title={row.name}
            >
              {row.name}
            </a>
          ) : (
            <span className="min-w-0 flex-1 truncate text-[13px]" title={row.name}>
              {row.name}
            </span>
          )}
          <span className="display shrink-0 text-[16px]">{money(row.price)}</span>
        </div>
        <div className="mono mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-[var(--text-dim)]">
          {unit && <span>{unit}</span>}
          {row.eta_min !== null && <span>{row.eta_min} min</span>}
          <span style={{ color: row.in_stock ? "var(--text-dim)" : "var(--bad)" }}>
            {row.in_stock ? "in stock" : "out of stock"}
          </span>
          {row.sponsored && (
            <span
              className="kicker px-1"
              style={{ border: "1px solid var(--warn)", color: "var(--warn)" }}
              title="the site marked this listing as sponsored"
            >
              AD
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

/** Every row this universe returned in this run, cheapest first. Read from the
 *  stored comparison rather than from the events: the events say what happened,
 *  the snapshot says what was found. */
function rowsFor(comparison: Comparison, universeId: string): NormalizedRow[] {
  const from = (groups: { rows: NormalizedRow[] }[]) =>
    groups.flatMap((g) => g.rows).filter((row) => row.universe === universeId);
  return [
    ...from(comparison.groups ?? []),
    ...from(comparison.unmatched ?? []),
    ...(comparison.demo_rows ?? []).filter((row) => row.universe === universeId),
  ].sort(byPriceAscending);
}

export default function PortalColumn({
  universe,
  state,
  comparison,
  query,
  pincode,
  latestEvent,
  landing,
  now,
}: {
  universe: Universe;
  state: UniverseState | undefined;
  comparison: Comparison;
  query: string;
  pincode: string | null;
  latestEvent: RunEvent | undefined;
  /** No run is showing. The column explains what it will do instead of
   *  pretending to be waiting on something. */
  landing: boolean;
  now: number;
}) {
  const reading = read(state, pincode, now);
  const rows = landing ? [] : rowsFor(comparison, universe.id);
  const search = fillSearchUrl(universe.search_url_template, query, pincode ?? "");
  const isDemo = universe.badge === "chaos";
  const active =
    !landing &&
    (state?.status === "triggered" ||
      state?.status === "collecting" ||
      state?.status === "rows" ||
      state?.status === "dispatched");
  const resolvedArea = rows.find((row) => row.resolved_area)?.resolved_area ?? null;

  return (
    <section
      className={`panel flex min-w-0 flex-col ${active ? "portal--active" : ""}`}
      style={{ ["--hue" as string]: universe.color }}
      aria-label={`${universe.display}: ${reading.word}`}
    >
      <header className="shrink-0 p-3">
        <div className="flex items-start gap-3">
          <Aperture
            status={landing ? "idle" : (state?.status ?? "idle")}
            tone={landing ? "hue" : reading.tone}
            count={state?.rowsKept ?? state?.rows ?? null}
          />
          <div className="min-w-0 flex-1">
            <h3 className="display truncate text-[15px]" style={{ color: universe.color }}>
              {universe.display}
            </h3>
            <p className="kicker mt-1" style={{ color: TONE_COLOR[reading.tone] }}>
              {landing ? "READY" : reading.word}
            </p>
            {/* Two lines' worth of room whether the fact needs one or two, so
                four columns in the same state keep their rules on one line. */}
            <p className="mt-1 min-h-[2.6em] text-[12px] leading-snug text-[var(--text-dim)]">
              {landing
                ? isDemo
                  ? "a store this app serves itself, so a collector can be broken and repaired on demand"
                  : "opens the site, sets your pincode, reads the shelf the site itself fetches"
                : reading.fact}
            </p>
            {!landing && state?.status === "validated" && resolvedArea && (
              // The site's OWN words for where it thinks we are, next to our own
              // pincode and never instead of it: one is their claim, one is ours.
              <p
                className="mt-1 truncate text-[12px] text-[var(--text-dim)]"
                title={resolvedArea}
              >
                “{resolvedArea}”
              </p>
            )}
            {!landing && state?.retriggeredAfterS != null && (
              <p
                className="kicker mt-2 inline-block px-1.5 py-0.5"
                style={{ border: "1px solid var(--warn)", color: "var(--warn)" }}
              >
                2ND TRY · retriggered after {state.retriggeredAfterS}s
              </p>
            )}
            {!landing && state?.artifactError && (
              <p className="mt-1.5 text-[11px]" style={{ color: "var(--warn)" }}>
                no page capture: {state.artifactError}. The rows are unaffected.
              </p>
            )}
          </div>
        </div>
        {!landing && state?.heal && <HealStrip heal={state.heal} />}
      </header>

      <div
        className="flex shrink-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-3 py-2"
        style={{ borderTop: "1px solid var(--hair)" }}
      >
        <span className="kicker text-[var(--text-dim)]">
          {landing || rows.length === 0
            ? "no listings yet"
            : `${plural(rows.length, "listing")}, cheapest first`}
        </span>
        {search && (
          <a
            href={search}
            target="_blank"
            rel="noreferrer noopener"
            className="link text-[12px]"
          >
            open this search on {siteName(universe.display)}
          </a>
        )}
      </div>

      {isDemo && !landing && rows.length > 0 && (
        <p className="shrink-0 px-3 pb-2 text-[11px]" style={{ color: "var(--warn)" }}>
          demo store, invented prices. Never matched against the real universes.
        </p>
      )}

      <ul className="shelf">
        {rows.map((row, i) => (
          <ShelfCard key={`${row.product_id ?? row.name}-${i}`} row={row} index={i} />
        ))}
      </ul>

      <footer
        className="mono flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-[11px] text-[var(--text-dim)]"
        style={{ borderTop: "1px solid var(--hair)" }}
      >
        {state?.screenshot && (
          <a
            href={state.screenshot.url}
            target="_blank"
            rel="noreferrer noopener"
            className="link"
          >
            screenshot{state.screenshot.placeholder ? " (placeholder)" : ""}
          </a>
        )}
        <span className="min-w-0 flex-1 truncate" title={latestEvent ? latestEvent.type : undefined}>
          {latestEvent
            ? `${istClock(latestEvent.ts) ?? latestEvent.ts} ${latestEvent.type}`
            : "no event yet"}
        </span>
      </footer>
    </section>
  );
}
