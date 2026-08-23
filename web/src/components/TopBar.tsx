export type Tab = "demo" | "live";

/** Band 0. The wordmark, the one control that decides what the page is doing,
 *  and the two facts a reader needs before believing anything below: which mode
 *  the server is in, and which pincode the numbers are for. */
export default function TopBar({
  tab,
  onTab,
  onGuide,
  mode,
  pincode,
  replay,
}: {
  tab: Tab;
  onTab: (tab: Tab) => void;
  /** Opens the "what is this?" guide modal. */
  onGuide: () => void;
  mode: string;
  /** The pincode of the run currently on screen, or null when none is. */
  pincode: string | null;
  /** What is on screen is a re-stream of a stored capture. The mode chip has to
   *  say so: a capture taken in live mode is still not live now, and this chip
   *  is the most prominent claim on the page. */
  replay: boolean;
}) {
  const mock = mode === "mock";

  return (
    <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 py-4">
      <div className="min-w-0">
        <h1 className="wordmark">
          <a href="/" className="text-inherit no-underline" title="back to the start">
            EKDAAM
          </a>
        </h1>
        <p className="kicker mt-1.5 text-[var(--text-dim)]">
          one product, every universe, one pincode
        </p>
      </div>

      <div className="seg" role="group" aria-label="what to show">
        <button type="button" aria-pressed={tab === "demo"} onClick={() => onTab("demo")}>
          Watch a demo
        </button>
        <button type="button" aria-pressed={tab === "live"} onClick={() => onTab("live")}>
          Try it live
        </button>
      </div>

      <div className="mono flex items-center gap-3 text-[12px]">
        <button type="button" className="link" onClick={onGuide}>
          what is this?
        </button>
        {pincode && (
          <span style={{ color: "var(--text-dim)" }}>
            pincode <span style={{ color: "var(--text)" }}>{pincode}</span>
          </span>
        )}
        <span
          className="kicker px-2 py-1"
          style={{
            border: `1px solid ${mock ? "var(--warn)" : "var(--hair)"}`,
            color: mock ? "var(--warn)" : "var(--text-dim)",
          }}
          title={
            replay
              ? "a stored capture being re-streamed. Nothing on screen is happening now"
              : mock
                ? "BD_MODE=mock: rows come from a committed fixture, not from a live collector"
                : "BD_MODE=live: every row was read off the site by a collector"
          }
        >
          mode · {replay ? `replay of ${mode || "unknown"}` : mode || "unknown"}
        </span>
      </div>
    </header>
  );
}
