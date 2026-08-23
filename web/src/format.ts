// Formatting the UI agrees on. Nothing here invents a value: a missing number
// renders as an em-free dash, never as a zero.

import type { ComparisonGroup, NormalizedRow } from "./types";

const RUPEES = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

/** `₹65`, `₹1,299.50`. Null prices are the caller's problem to lay out, so they
 *  come back as a dash rather than as a plausible-looking number. */
export function money(value: number | null): string {
  return value === null ? "-" : `₹${RUPEES.format(value)}`;
}

export function packLabel(group: ComparisonGroup): string {
  if (group.qty === null || !group.unit) return "unknown pack";
  return `${group.qty % 1 === 0 ? group.qty : group.qty.toFixed(2)} ${group.unit}`;
}

/** The group's own left-hand label on a payline: what the resolver actually
 *  agreed on, and nothing more. */
export function groupLabel(group: ComparisonGroup): string {
  const parts = [group.brand ?? "no brand parsed", packLabel(group)];
  if (group.variant) parts.push(group.variant);
  return parts.join(" · ");
}

/** "Zepto-verse" is what this app calls the universe; "Zepto" is what the link
 *  actually opens, and a link should say where it goes. */
export function siteName(display: string): string {
  return display.replace(/-verse$/i, "");
}

/** Every URL pattern lives on the server (server/product_links.py): a universe
 *  arrives with its search template and a row with its `product_url` already
 *  built. Nothing here invents a link. */
export function fillSearchUrl(
  template: string | null | undefined,
  query: string,
  pincode: string,
): string | null {
  if (!template) return null;
  return template
    .replace("{query}", encodeURIComponent(query))
    .replace("{pincode}", encodeURIComponent(pincode));
}

export function unitPriceLabel(row: NormalizedRow): string | null {
  if (row.unit_price === null || !row.unit_price_basis) return null;
  return `${money(row.unit_price)} ${row.unit_price_basis}`;
}

/** The lowest real price among these rows, or null when none of them carries
 *  one. Used for the lit cell on a payline and for the basket sums. */
export function cheapestPrice(rows: NormalizedRow[]): number | null {
  const prices = rows.map((row) => row.price).filter((p): p is number => p !== null);
  return prices.length === 0 ? null : Math.min(...prices);
}

/** Cheapest first, then the rows with no price at all. A shelf is read top
 *  down, so the order is the one a shopper is looking for; it is derived
 *  entirely from the prices the collectors returned. */
export function byPriceAscending(a: NormalizedRow, b: NormalizedRow): number {
  if (a.price === null && b.price === null) return 0;
  if (a.price === null) return 1;
  if (b.price === null) return -1;
  return a.price - b.price;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** A universe's hue as TEXT on the light deck. Blinkit's yellow is unreadable
 *  as-is on a pale surface, so every hue is pulled a third of the way to ink;
 *  fills and rings keep the raw hue. */
export function hueInk(color: string): string {
  return `color-mix(in srgb, ${color} 68%, #151a26)`;
}
