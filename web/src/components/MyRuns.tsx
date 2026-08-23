import { istClock } from "../time";
import type { Cart, RunMeta } from "../types";

/** This browser's own captures, newest first.
 *
 *  "My" is anonymous scoping, NOT an account: the server hands every browser one
 *  opaque cookie and a run remembers only its hash, so this list is whatever this
 *  browser has run. Clearing cookies makes a new identity and empties it. Nothing
 *  here is private in the security sense and the panel says so.
 */
export default function MyRuns({
  runs,
  carts,
  onReplayRun,
  onReplayCart,
  busy,
  showing,
}: {
  runs: RunMeta[];
  carts: Cart[];
  onReplayRun: (runId: string) => void;
  onReplayCart: (cart: Cart) => void;
  busy: boolean;
  /** Run ids currently on screen. */
  showing: string[];
}) {
  const inACart = new Set(carts.flatMap((cart) => cart.items.map((item) => item.run_id)));
  // A cart's own runs are listed once, as the cart. Listing them again on their
  // own would offer a replay of one quarter of a basket.
  const loose = runs.filter((run) => run.status === "done" && !inACart.has(run.run_id));

  if (loose.length === 0 && carts.length === 0) {
    return (
      <p className="text-[12px] text-[var(--text-dim)]">
        Nothing from this browser yet. A run you start appears here, ready to replay for free.
      </p>
    );
  }

  return (
    <section className="panel">
      <header
        className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-2"
        style={{ borderBottom: "1px solid var(--hair)" }}
      >
        <h2 className="kicker">My runs and carts</h2>
        <p className="text-[11px] text-[var(--text-dim)]">
          This browser only, scoped by an anonymous cookie, not an account. Replays cost
          nothing.
        </p>
      </header>
      <div className="scroll-hint-ink">
        <ul className="scroll-in" style={{ maxHeight: "184px" }}>
          {carts.map((cart) => (
            <li
              key={cart.cart_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-[13px]"
              style={{ borderTop: "1px solid var(--hair)" }}
            >
              <span className="kicker" style={{ color: "var(--text-dim)" }}>
                CART
              </span>
              <span className="min-w-0 max-w-[22rem] truncate">
                {cart.items.map((item) => item.item).join(", ")}
              </span>
              <span className="mono mr-auto text-[11px] text-[var(--text-dim)]">
                {cart.pincode} · {istClock(cart.created_at) ?? cart.created_at}
              </span>
              <button
                type="button"
                onClick={() => onReplayCart(cart)}
                disabled={busy}
                className="ctl py-1 text-[12px]"
              >
                Replay cart
              </button>
            </li>
          ))}
          {loose.map((run) => (
            <li
              key={run.run_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-[13px]"
              style={{ borderTop: "1px solid var(--hair)" }}
            >
              <span className="min-w-0 max-w-[16rem] truncate">{run.query}</span>
              {run.replay && (
                <span className="kicker" style={{ color: "var(--warn)" }}>
                  REPLAY
                </span>
              )}
              {showing.includes(run.run_id) && (
                <span className="kicker" style={{ color: "var(--text-dim)" }}>
                  SHOWING
                </span>
              )}
              <span className="mono mr-auto text-[11px] text-[var(--text-dim)]">
                {run.pincode || "no pincode"} · {istClock(run.created_at) ?? run.created_at}
              </span>
              {/* A replay of a replay is refused by the server, so it is not
                offered: the original capture is the one to re-stream. */}
              {!run.replay && (
                <button
                  type="button"
                  onClick={() => onReplayRun(run.run_id)}
                  disabled={busy}
                  className="ctl py-1 text-[12px]"
                >
                  Replay
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
