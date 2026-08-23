import { useRef, useState } from "react";

export const MAX_ITEMS = 6;
const PINCODE_LENGTH = 6;

/** One product, or up to six of them at one pincode.
 *
 *  Two products is not a different feature: it is a cart, which the server fans
 *  out into one ordinary run per item. The form says so, because each item spends
 *  the same collector budget a single run does.
 */
export default function CompareForm({
  onCompare,
  busy,
  inFlight,
  allowlist,
}: {
  onCompare: (items: string[], pincode: string) => void;
  busy: boolean;
  inFlight: boolean;
  /** Set on a server that only accepts certain queries. Empty means anything. */
  allowlist: string[];
}) {
  const [items, setItems] = useState<string[]>([]);
  const [draft, setDraft] = useState("amul butter");
  const [pincode, setPincode] = useState("560001");
  const input = useRef<HTMLInputElement>(null);

  const trimmed = draft.trim();
  const all = [...items, trimmed].filter((item) => item.length > 0);
  const pincodeReady = pincode.length === PINCODE_LENGTH;
  const full = items.length >= MAX_ITEMS;
  const canAdd = trimmed.length >= 2 && !full && items.length + 1 < MAX_ITEMS + 1;

  function addChip() {
    if (!canAdd) return;
    // Case-insensitive, because the server removes duplicates that way and a
    // chip the server would silently drop should never have been added here.
    if (items.some((item) => item.toLowerCase() === trimmed.toLowerCase())) {
      setDraft("");
      return;
    }
    setItems([...items, trimmed]);
    setDraft("");
    input.current?.focus();
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy || inFlight || !pincodeReady || all.length === 0) return;
    onCompare(all, pincode);
  }

  const why = inFlight
    ? "a run is already in flight"
    : !pincodeReady
      ? `a pincode is ${PINCODE_LENGTH} digits`
      : all.length === 0
        ? "name at least one product"
        : undefined;

  return (
    <form onSubmit={submit} className="panel p-4">
      {items.length > 0 && (
        <ul className="mb-3 flex flex-wrap gap-2">
          {items.map((item, i) => (
            <li key={`${item}-${i}`} className="chip">
              <span>{item}</span>
              <button
                type="button"
                onClick={() => setItems(items.filter((_, j) => j !== i))}
                aria-label={`remove ${item}`}
                className="px-1 text-[var(--text-dim)] hover:text-[var(--text)]"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-end gap-3">
        {/* Capped rather than greedy: a field the width of the page for two
            words reads as a form that has lost its shape. */}
        <label className="flex min-w-0 flex-1 basis-64 flex-col gap-1 sm:max-w-md">
          <span className="kicker text-[var(--text-dim)]">
            {items.length > 0 ? `Product ${items.length + 1}` : "Product"}
          </span>
          <input
            ref={input}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter adds a chip only when there is already a cart going;
              // otherwise Enter submits, which is what a one-product form should
              // do.
              if (e.key === "Enter" && items.length > 0 && canAdd) {
                e.preventDefault();
                addChip();
              }
            }}
            className="field w-full"
            maxLength={60}
            placeholder="amul butter"
            disabled={full}
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="kicker text-[var(--text-dim)]">Pincode</span>
          <input
            value={pincode}
            onChange={(e) => setPincode(e.target.value.replace(/\D/g, ""))}
            className="field mono w-28"
            inputMode="numeric"
            maxLength={PINCODE_LENGTH}
            placeholder="6 digits"
          />
        </label>

        <button
          type="button"
          onClick={addChip}
          disabled={!canAdd}
          className="ctl"
          title={
            full
              ? `a cart holds ${MAX_ITEMS} products`
              : trimmed.length < 2
                ? "type a product first"
                : "add this product and compare them together"
          }
        >
          + add another product
        </button>

        <button
          type="submit"
          disabled={busy || inFlight || !pincodeReady || all.length === 0}
          title={why}
          className="ctl ctl-strong"
        >
          {busy ? "Comparing..." : all.length > 1 ? `Compare ${all.length} products` : "Compare"}
        </button>
      </div>

      <p className="mt-3 text-[12px] text-[var(--text-dim)]">
        Every product is one real collector job per universe, at this pincode, right now.
        {items.length > 0
          ? ` ${items.length + (trimmed ? 1 : 0)} of ${MAX_ITEMS} in this cart.`
          : ""}
        {allowlist.length > 0 && ` This server accepts: ${allowlist.join(", ")}.`}
      </p>
    </form>
  );
}
