// Mirrors server/events.py, server/resolve.py and server/runs.py.

export type EventType =
  | "run_requested"
  | "universe_dispatched"
  | "triggered"
  | "progress"
  | "rows"
  | "screenshot"
  | "artifact_failed"
  | "validated"
  | "zero_rows"
  // non-terminal: the collector job never started, so it was canceled and one
  // fresh job was triggered for the same universe
  | "retriggered"
  | "failed"
  | "timed_out"
  | "done"
  // reserved, defined but not emitted yet
  | "incident"
  // the self-heal cycle (POST /api/chaos/heal)
  | "heal_started"
  | "heal_previewed"
  | "heal_approved"
  | "heal_promoted";

export interface RunEvent {
  i: number;
  ts: string;
  run_id: string;
  universe: string | null;
  type: EventType;
  replay: boolean;
  data: Record<string, unknown>;
}

export interface Universe {
  id: string;
  display: string;
  color: string;
  badge: "live" | "chaos" | "replay-only";
  collector_version: string;
  /** The site's own search, as a template taking `{query}` and `{pincode}`.
   *  Shown once in the universe's header, never per row. Null = no search link.
   *  See server/product_links.py. */
  search_url_template: string | null;
  trigger_mode: string;
  mapper: string;
  wired: boolean;
  dispatchable: boolean;
  status: string;
}

export interface UniversesResponse {
  mode: string;
  collector_version: string;
  universes: Universe[];
  query_allowlist: string[];
}

export interface NormalizedRow {
  universe: string;
  name: string;
  brand: string | null;
  variant: string | null;
  qty: number | null;
  unit: string | null;
  price: number | null;
  mrp: number | null;
  unit_price: number | null;
  unit_price_basis: string | null;
  in_stock: boolean;
  qty_available: number | null;
  eta_min: number | null;
  sponsored: boolean;
  product_id: string | null;
  /** The row's own listing on the site it came from, built server-side from its
   *  product id. Null when that universe has no verified URL pattern, or the row
   *  carries no id, in which case the name renders as plain text. See
   *  server/product_links.py. */
  product_url: string | null;
  image_url: string | null;
  raw_ref: string | null;
  captured_at: string | null;
  resolved_area: string | null;
}

export interface ComparisonGroup {
  key: string;
  brand: string | null;
  qty: number | null;
  unit: string | null;
  variant: string | null;
  /** "close" = same brand token, same base pack size, same variant, and the
   *  product names agree. Deliberately the strongest label there is: the
   *  resolver has no product identity to be certain about. See
   *  server/resolve.py. */
  confidence: "close" | "unmatched";
  universes: string[];
  rows: NormalizedRow[];
}

export interface Comparison {
  groups: ComparisonGroup[];
  unmatched: ComparisonGroup[];
  /** Real universes only. See `DEMO_UNIVERSES` in server/resolve.py. */
  row_count: number;
  universe_count: number;
  /** Rows from a demo universe (`chaos`), which never enter a comparison group:
   *  the store is one this app serves itself, so its prices are invented. Shown
   *  under their own heading, never matched. */
  demo_rows: NormalizedRow[];
}

export interface RunMeta {
  run_id: string;
  query: string;
  pincode: string;
  area_label: string;
  mode: string;
  created_at: string;
  status: string;
  replay: boolean;
  source_run_id: string | null;
  universes: string[];
  finished_at: string | null;
}

export interface RunSnapshot {
  meta: RunMeta;
  events: RunEvent[];
  comparison: Comparison;
}
