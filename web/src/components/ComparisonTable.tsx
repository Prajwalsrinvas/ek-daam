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

function Cell({ row }: { row: NormalizedRow }) {
  return (
    <div className="space-y-0.5">
      {/* The listing's OWN name leads the cell. The row header can only say what
          the group key agreed on (brand, pack, variant); the thing a reader
          needs in order to judge whether these really are the same product is
          what each site actually called it. */}
      <div className="font-medium text-slate-900" title={row.name}>
        {row.name}
      </div>
      <div className="flex items-baseline gap-2">
        <span className="font-semibold">{money(row.price)}</span>
        {row.mrp !== null && row.price !== null && row.mrp > row.price && (
          <span className="text-xs text-slate-400 line-through">{money(row.mrp)}</span>
        )}
      </div>
      <div className="text-xs text-slate-500">
        {row.unit_price !== null ? `${money(row.unit_price)} ${row.unit_price_basis}` : "unit price n/a"}
      </div>
      <div className="text-xs text-slate-500">
        {row.in_stock ? (
          <span className="text-emerald-700">in stock{row.qty_available !== null ? ` (${row.qty_available})` : ""}</span>
        ) : (
          <span className="text-red-700">out of stock</span>
        )}
        {row.eta_min !== null && <span className="ml-2">· {row.eta_min} min listed</span>}
        {row.sponsored && <span className="ml-2 rounded bg-amber-100 px-1 text-amber-800">sponsored</span>}
      </div>
      {/* Part of the receipt: two prices are only comparable if you know when
          each was read off the shelf. Shown per cell because universes capture
          at different moments, and in IST like every other clock in the app. */}
      {istClock(row.captured_at) && (
        <div className="text-xs text-slate-400" title={row.captured_at ?? undefined}>
          captured {istClock(row.captured_at)}
        </div>
      )}
      {row.resolved_area && (
        // The site's OWN words for where it thinks we are. Shown next to the
        // area label we configured, never instead of it — one is their claim,
        // the other is ours, and conflating them would hide a disagreement.
        <div className="truncate text-xs text-slate-400" title={row.resolved_area}>
          site resolved: {row.resolved_area}
        </div>
      )}
    </div>
  );
}

function GroupTable({
  title, subtitle, groups, universeIds, registry,
}: {
  title: string;
  subtitle: string;
  groups: ComparisonGroup[];
  universeIds: string[];
  registry: Universe[];
}) {
  const display = new Map(registry.map((u) => [u.id, u.display]));
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
              <th className="px-3 py-2">Product</th>
              {universeIds.map((id) => (
                <th key={id} className="px-3 py-2">{display.get(id) ?? id}</th>
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
                            <Cell key={`${row.product_id ?? row.name}-${index}`} row={row} />
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

export default function ComparisonTable({
  comparison,
  registry,
}: {
  comparison: Comparison;
  registry: Universe[];
}) {
  const all = [...comparison.groups, ...comparison.unmatched];
  const universeIds = [...new Set(all.flatMap((g) => g.universes))].sort();

  if (all.length === 0) {
    return (
      <p className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
        No comparison yet — rows appear here once a universe validates.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <GroupTable
        title="Close matches across universes"
        subtitle="Same brand & pack, names agree — a heuristic, not a product identity, so check the names in each cell. Shelf price only: no fees, no delivery charges, no member pricing."
        groups={comparison.groups}
        universeIds={universeIds}
        registry={registry}
      />
      <GroupTable
        title={`Single source (${comparison.unmatched.length})`}
        subtitle="One universe only — either nobody else listed it, or its pack size could not be parsed, and an unknown pack is never matched across universes. Shown, not matched."
        groups={comparison.unmatched}
        universeIds={universeIds}
        registry={registry}
      />
    </div>
  );
}
