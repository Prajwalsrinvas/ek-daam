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
  row_count: number;
  universe_count: number;
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
