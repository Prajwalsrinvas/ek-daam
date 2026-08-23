import type { Cart, RunMeta, RunSnapshot, UniversesResponse } from "./types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export function getUniverses(): Promise<UniversesResponse> {
  return fetch("/api/universes").then(json<UniversesResponse>);
}

export function getRuns(): Promise<{ runs: RunMeta[] }> {
  return fetch("/api/runs").then(json<{ runs: RunMeta[] }>);
}

export function getRun(runId: string): Promise<RunSnapshot> {
  return fetch(`/api/runs/${runId}`).then(json<RunSnapshot>);
}

export function startRun(query: string, pincode: string): Promise<{ run_id: string; meta: RunMeta }> {
  return fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, pincode }),
  }).then(json<{ run_id: string; meta: RunMeta }>);
}

export function startReplay(runId: string): Promise<{ run_id: string; meta: RunMeta }> {
  return fetch(`/api/replays/${runId}`, { method: "POST" }).then(
    json<{ run_id: string; meta: RunMeta }>,
  );
}

export function eventsUrl(runId: string): string {
  return `/api/runs/${runId}/events`;
}

/** N products at one pincode. The server fans the cart out into one ordinary run
 *  per item and answers with their ids in item order; there is no cart-level
 *  stream, so the UI subscribes to each run. */
export function startCart(items: string[], pincode: string): Promise<Cart> {
  return fetch("/api/carts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items, pincode }),
  }).then(json<Cart>);
}

export function getCarts(): Promise<{ carts: Cart[] }> {
  return fetch("/api/carts").then(json<{ carts: Cart[] }>);
}

export function getCart(cartId: string): Promise<Cart> {
  return fetch(`/api/carts/${cartId}`).then(json<Cart>);
}
