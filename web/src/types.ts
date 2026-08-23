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
  // the additional, labelled model matching layer. Run-level (`universe: null`),
  // emitted after the deterministic comparison and BEFORE `done`. It never
  // changes the receipt: it only adds groups of its own, labelled as its own.
  | "llm_match"
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
  /** The one capture anyone may replay without having run anything. Null when
   *  none is configured, and the UI offers no demo button then. */
  demo_run_id: string | null;
  /** Every capture this server offers publicly, newest thinking first. A server
   *  with none configured sends an empty list and the UI says so rather than
   *  hiding the tab. Old servers omit the field entirely, so every read of it
   *  goes through `?? []`. */
  demo: DemoEntry[];
}

/** One publicly replayable capture. `kind` decides how many runs it drives:
 *  a `run` is one, a `cart` is one per item in item order, a `story` is an
 *  ordered set of chapters. */
export interface DemoEntry {
  id: string;
  title: string;
  /** One line saying what to look for. Shown on the card, never invented here. */
  note: string;
  kind: "run" | "cart" | "story";
  run_ids: string[];
  /** Cart only, same order as `run_ids`. */
  items: string[] | null;
  /** Story only, one label per run id. */
  chapters: string[] | null;
}

/** N products at one pincode = N ordinary runs, fanned out. There is no
 *  cart-level event stream: the UI subscribes to each run and a cart is replayed
 *  by replaying each of its runs. */
export interface CartItem {
  item: string;
  run_id: string;
}

export interface Cart {
  cart_id: string;
  pincode: string;
  created_at: string;
  items: CartItem[];
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
  /** Rules groups: "close" = same brand token, same base pack size, same
   *  variant, and the product names agree. Deliberately the strongest label
   *  there is: the resolver has no product identity to be certain about. See
   *  server/resolve.py. Model groups carry "high" or "low" instead, and the UI
   *  never prints either without the word "model-suggested" next to it. */
  confidence: "close" | "unmatched" | "high" | "low";
  /** Who aligned this group. Absent on a run captured before the model layer
   *  existed, which is why every read defaults it to "rules". */
  source: "rules" | "model";
  universes: string[];
  rows: NormalizedRow[];
}

/** The model layer's own report, as the snapshot carries it. Null on a run that
 *  predates the layer; `status: "skipped"` when it was switched off or had
 *  nothing to do, with the reason spelled out. */
export interface LlmReport {
  status: "started" | "done" | "failed" | "skipped" | null;
  model: string | null;
  seconds: number | null;
  blocks: number | null;
  rows_sent: number | null;
  accepted: number | null;
  rejected: number | null;
  reason: string | null;
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
  /** What the MODEL adds on top of the deterministic groups, and only that: a
   *  model group that duplicates a rules group is dropped server-side. Rows
   *  only, joined back from the captured rows: the model never supplies a name
   *  or a price. Old runs carry none, so every read goes through `?? []`. */
  llm_groups: ComparisonGroup[];
  llm: LlmReport | null;
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
// No owner field: a run is scoped server-side by an anonymous cookie and the
// hash never leaves the process, so every run the UI receives is already one
// this browser may see. See server/owner.py.


export interface RunSnapshot {
  meta: RunMeta;
  events: RunEvent[];
  comparison: Comparison;
}
