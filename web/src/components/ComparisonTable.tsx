import { istClock } from "../time";
import type { Comparison, ComparisonGroup, NormalizedRow, Universe } from "../types";

function money(value: number | null): string {
  return value === null ? "—" : `₹${value.toFixed(2)}`;
}

function packLabel(group: ComparisonGroup): string {
  if (group.qty === null || !group.unit) return "unknown pack";
  return `${group.qty % 1 === 0 ? group.qty : group.qty.toFixed(2)} ${group.unit}`;
}

/** The server's label, spelled out. Never stronger than "close": the resolver
 *  compares a brand token, a pack size, a variant and the words in the names —
 *  it has no product identity to be certain about. */
const CONFIDENCE_LABEL: Record<ComparisonGroup["confidence"], string> = {
  close: "close match",
  unmatched: "unmatched",
};

/** Every URL pattern lives on the server (server/product_links.py): a row
 *  arrives with its `product_url` already built from the site's own product id,
 *  and a universe arrives with its search template. Nothing here invents a link,
 *  so switching a site off is one edit in one file and this component keeps
 *  working, because it renders plain text wherever a link is null. */
function fillSearchUrl(
  template: string | null,
  query: string,
  pincode: string,
): string | null {
  if (!template) return null;
  return template
    .replace("{query}", encodeURIComponent(query))
    .replace("{pincode}", encodeURIComponent(pincode));
}

/** "Zepto-verse" is what this app calls the universe; "Zepto" is what the link
 *  actually opens, and the link should say where it goes. */
function siteName(display: string): string {
  return display.replace(/-verse$/i, "");
}

function Thumb({ row }: { row: NormalizedRow }) {
  // A missing image is normal, not every collector returns one, so the slot
  // keeps its size and stays blank rather than showing a broken-image icon.
  if (!row.image_url) {
    return <div className="h-10 w-10 shrink-0 rounded border border-slate-200 bg-slate-50" />;
  }
  return (
    <img
      src={row.image_url}
      alt=""
      loading="lazy"
      className="h-10 w-10 shrink-0 rounded border border-slate-200 object-cover"
    />
  );
}

/** Fields whose value is the same on every row a universe returned in this run.
 *  Printed once in that universe's column header instead of on every cell: they
 *  are facts about the capture, not about the product, and repeating them thirty
 *  times buries the numbers that actually differ. A field is only hoisted when
 *  it is present and identical throughout. One disagreement and it goes back to
 *  the cells, because the disagreement is the interesting part. */
interface UniverseCommon {
  resolvedArea: string | null;
  etaMin: number | null;
  capturedAt: string | null;
}

const NOTHING_COMMON: UniverseCommon = { resolvedArea: null, etaMin: null, capturedAt: null };

function shared<T>(rows: NormalizedRow[], read: (row: NormalizedRow) => T | null): T | null {
  if (rows.length === 0) return null;
  const first = read(rows[0]);
  if (first === null || first === undefined) return null;
  return rows.every((row) => read(row) === first) ? first : null;
}

function commonByUniverse(rows: NormalizedRow[]): Record<string, UniverseCommon> {
  const byUniverse = new Map<string, NormalizedRow[]>();
  rows.forEach((row) => {
    const bucket = byUniverse.get(row.universe);
    if (bucket) bucket.push(row);
    else byUniverse.set(row.universe, [row]);
  });

  const out: Record<string, UniverseCommon> = {};
  byUniverse.forEach((universeRows, id) => {
    out[id] = {
      resolvedArea: shared(universeRows, (r) => r.resolved_area),
      etaMin: shared(universeRows, (r) => r.eta_min),
      capturedAt: shared(universeRows, (r) => r.captured_at),
    };
  });
  return out;
}

function UniverseHeading({
  id,
  universe,
  common,
  query,
  pincode,
}: {
  id: string;
  universe: Universe | undefined;
  common: UniverseCommon;
  query: string;
  pincode: string;
}) {
  const display = universe?.display ?? id;
  const search = fillSearchUrl(universe?.search_url_template ?? null, query, pincode);

  return (
    <div className="space-y-0.5">
      <div>{display}</div>
      {/* ONE search link per universe, here and nowhere else. Beside a listing
          it would read as a link TO that listing; here it plainly means "the
          same words, on their site". */}
      {search && (
        <a
          href={search}
          target="_blank"
          rel="noreferrer noopener"
          className="block font-normal normal-case text-blue-700 underline"
        >
          open this search on {siteName(display)}
        </a>
      )}
      {(common.etaMin !== null || common.capturedAt || common.resolvedArea) && (
        <div className="space-y-0.5 font-normal normal-case text-slate-400">
          {common.etaMin !== null && <div>{common.etaMin} min listed</div>}
          {istClock(common.capturedAt) && (
            <div title={common.capturedAt ?? undefined}>captured {istClock(common.capturedAt)}</div>
          )}
          {common.resolvedArea && (
            // The site's OWN words for where it thinks we are. Shown next to the
            // area label we configured, never instead of it — one is their claim,
            // the other is ours, and conflating them would hide a disagreement.
            <div className="max-w-[15rem] truncate" title={common.resolvedArea}>
              site resolved: {common.resolvedArea}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Cell({ row, common }: { row: NormalizedRow; common: UniverseCommon }) {
  return (
    <div className="flex gap-2">
      <Thumb row={row} />
      <div className="min-w-0 space-y-0.5">
        {/* The listing's OWN name leads the cell. The row header can only say what
            the group key agreed on (brand, pack, variant); the thing a reader
            needs in order to judge whether these really are the same product is
            what each site actually called it. It is also the link to the listing
            itself when the server could build one, and plain text when it could
            not: a name that is never a link is better than one that sometimes
            opens the wrong product. */}
        {row.product_url ? (
          <a
            href={row.product_url}
            target="_blank"
            rel="noreferrer noopener"
            className="font-medium text-blue-800 underline decoration-blue-200 underline-offset-2 hover:decoration-blue-500"
            title={row.name}
          >
            {row.name}
          </a>
        ) : (
          <div className="font-medium text-slate-900" title={row.name}>
            {row.name}
          </div>
        )}
        <div className="flex items-baseline gap-2">
          <span className="font-semibold">{money(row.price)}</span>
          {row.mrp !== null && row.price !== null && row.mrp > row.price && (
            <span className="text-xs text-slate-400 line-through">{money(row.mrp)}</span>
          )}
        </div>
        <div className="text-xs text-slate-500">
          {row.unit_price !== null
            ? `${money(row.unit_price)} ${row.unit_price_basis}`
            : "unit price n/a"}
        </div>
        <div className="text-xs text-slate-500">
          {row.in_stock ? (
            <span className="text-emerald-700">
              in stock{row.qty_available !== null ? ` (${row.qty_available})` : ""}
            </span>
          ) : (
            <span className="text-red-700">out of stock</span>
          )}
          {/* Only when it differs across this universe's rows; a value they all
              share is printed once in the column header. Same for the two below. */}
          {common.etaMin === null && row.eta_min !== null && (
            <span className="ml-2">· {row.eta_min} min listed</span>
          )}
          {row.sponsored && (
            <span className="ml-2 rounded bg-amber-100 px-1 text-amber-800">sponsored</span>
          )}
        </div>
        {common.capturedAt === null && istClock(row.captured_at) && (
          // Part of the receipt: two prices are only comparable if you know when
          // each was read off the shelf. In IST, like every other clock here.
          <div className="text-xs text-slate-400" title={row.captured_at ?? undefined}>
            captured {istClock(row.captured_at)}
          </div>
        )}
        {common.resolvedArea === null && row.resolved_area && (
          <div className="truncate text-xs text-slate-400" title={row.resolved_area}>
            site resolved: {row.resolved_area}
          </div>
        )}
      </div>
    </div>
  );
}

function GroupTable({
  title,
  subtitle,
  groups,
  universeIds,
  registry,
  common,
  query,
  pincode,
}: {
  title: string;
  subtitle: string;
  groups: ComparisonGroup[];
  universeIds: string[];
  registry: Universe[];
  common: Record<string, UniverseCommon>;
  query: string;
  pincode: string;
}) {
  const byId = new Map(registry.map((u) => [u.id, u]));
  if (groups.length === 0) return null;

  return (
    <section className="space-y-2">
      <div>
        <h3 className="font-semibold">{title}</h3>
        <p className="text-xs text-slate-500">{subtitle}</p>
      </div>
      <div className="overflow-x-auto rounded border border-slate-200 bg-white">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2 align-top">Product</th>
              {universeIds.map((id) => (
                <th key={id} className="px-3 py-2 align-top">
                  <UniverseHeading
                    id={id}
                    universe={byId.get(id)}
                    common={common[id] ?? NOTHING_COMMON}
                    query={query}
                    pincode={pincode}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <tr key={group.key} className="border-t border-slate-100 align-top">
                <td className="px-3 py-3">
                  <div className="font-medium">{group.brand ?? "—"}</div>
                  <div className="text-xs text-slate-500">
                    {packLabel(group)}
                    {group.variant ? ` · ${group.variant}` : ""}
                  </div>
                  <div className="text-xs text-slate-400">{CONFIDENCE_LABEL[group.confidence]}</div>
                </td>
                {universeIds.map((id) => {
                  const rows = group.rows.filter((r) => r.universe === id);
                  return (
                    <td key={id} className="px-3 py-3">
                      {rows.length === 0 ? (
                        <span className="text-slate-300">—</span>
                      ) : (
                        <div className="space-y-3">
                          {rows.map((row, index) => (
                            <Cell
                              key={`${row.product_id ?? row.name}-${index}`}
                              row={row}
                              common={common[id] ?? NOTHING_COMMON}
                            />
                          ))}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** The demo universe, on its own. `chaos` points at the store this app serves
 *  itself, so its prices are invented. The resolver keeps its rows out of every
 *  comparison group (server/resolve.py), and they are shown here under a heading
 *  that says what they are. */
function DemoRows({
  rows,
  registry,
  common,
  query,
  pincode,
}: {
  rows: NormalizedRow[];
  registry: Universe[];
  common: Record<string, UniverseCommon>;
  query: string;
  pincode: string;
}) {
  const byId = new Map(registry.map((u) => [u.id, u]));
  const universeIds = [...new Set(rows.map((r) => r.universe))].sort();
  if (rows.length === 0) return null;

  return (
    <section className="space-y-2">
      <div>
        <h3 className="font-semibold">
          {universeIds.map((id) => byId.get(id)?.display ?? id).join(", ")} demo store, not
          compared
        </h3>
        <p className="text-xs text-slate-500">
          A store this app serves itself, so a collector can be broken and repaired on demand. Its
          products and prices are invented, so these rows are never matched against the real
          universes. They are here to show that the collector is reading the page.
        </p>
      </div>
      <div className="overflow-x-auto rounded border border-dashed border-slate-300 bg-white">
        <table className="w-full min-w-[480px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              {universeIds.map((id) => (
                <th key={id} className="px-3 py-2 align-top">
                  <UniverseHeading
                    id={id}
                    universe={byId.get(id)}
                    common={common[id] ?? NOTHING_COMMON}
                    query={query}
                    pincode={pincode}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-slate-100 align-top">
              {universeIds.map((id) => (
                <td key={id} className="px-3 py-3">
                  <div className="space-y-3">
                    {rows
                      .filter((row) => row.universe === id)
                      .map((row, index) => (
                        <Cell
                          key={`${row.product_id ?? row.name}-${index}`}
                          row={row}
                          common={common[id] ?? NOTHING_COMMON}
                        />
                      ))}
                  </div>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function ComparisonTable({
  comparison,
  registry,
  query,
  pincode,
}: {
  comparison: Comparison;
  registry: Universe[];
  /** What the run actually searched for. The per-universe search links reproduce
   *  that search on the site itself, so they use the run's words, not whatever
   *  is currently typed in the form. */
  query: string;
  pincode: string;
}) {
  const all = [...comparison.groups, ...comparison.unmatched];
  const universeIds = [...new Set(all.flatMap((g) => g.universes))].sort();
  const demoRows = comparison.demo_rows ?? [];
  const common = commonByUniverse([...all.flatMap((g) => g.rows), ...demoRows]);

  if (all.length === 0 && demoRows.length === 0) {
    return (
      <p className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
        No comparison yet. Rows appear here once a universe validates.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <GroupTable
        title="Close matches across universes"
        subtitle="Same brand & pack, names agree. A heuristic, not a product identity, so check the names in each cell. Shelf price only: no fees, no delivery charges, no member pricing."
        groups={comparison.groups}
        universeIds={universeIds}
        registry={registry}
        common={common}
        query={query}
        pincode={pincode}
      />
      <GroupTable
        title={`Single source (${comparison.unmatched.length})`}
        subtitle="One universe only: either nobody else listed it, or its pack size could not be parsed, and an unknown pack is never matched across universes. Shown, not matched."
        groups={comparison.unmatched}
        universeIds={universeIds}
        registry={registry}
        common={common}
        query={query}
        pincode={pincode}
      />
      <DemoRows
        rows={demoRows}
        registry={registry}
        common={common}
        query={query}
        pincode={pincode}
      />
    </div>
  );
}
