import { useEffect } from "react";

/** The "what is this?" modal. Opens only on click, explains the app, where the
 *  data comes from, and the chaos universe, in the fewest words that stay true. */
export default function Guide({ open, onClose }: { open: boolean; onClose: () => void }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(21, 26, 38, 0.45)" }}
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="What is EkDaam"
        className="max-h-[85vh] w-full max-w-xl overflow-y-auto p-6"
        style={{ background: "var(--paper-2)", border: "1px solid var(--hair)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4">
          <h2 className="wordmark text-[20px]">What is this?</h2>
          <button type="button" className="link mono text-[12px]" onClick={onClose} aria-label="Close">
            close
          </button>
        </div>

        <p className="kicker mt-5 text-[var(--text-dim)]">the app</p>
        <p className="mt-1.5 text-[14px] leading-relaxed">
          Type a grocery item and an Indian pincode. EkDaam checks Zepto, Blinkit and
          Instamart live and lays the shelves side by side, so the cheapest option is
          obvious. A cart of up to six items runs together, with the basket total per
          app.
        </p>

        <p className="kicker mt-5 text-[var(--text-dim)]">how matching works</p>
        <p className="mt-1.5 text-[14px] leading-relaxed">
          Two rows land on one line only when brand, pack size, variant and product
          name all agree, and the line is marked <em>close</em>, never <em>exact</em>.
          When a language model spots a pair the rules missed, its suggestion is drawn
          dashed, carries the model's own confidence, and is re-checked by the same
          rules. The model only ever points at rows a collector captured; it cannot
          invent a product or a price.
        </p>

        <p className="kicker mt-5 text-[var(--text-dim)]">where the data comes from</p>
        <p className="mt-1.5 text-[14px] leading-relaxed">
          This app has no scraping code of its own. Every search fires four Bright Data
          Scraper Studio collectors. Each one opens the real app, sets your pincode the
          way a customer would, and reads the product feed the site loads for itself. A
          row only counts after the site confirms it is delivering to your pincode.
        </p>

        <p className="kicker mt-5 text-[var(--text-dim)]">the chaos universe</p>
        <p className="mt-1.5 text-[14px] leading-relaxed">
          ChaosMart is a small fake store this app serves itself, so a scraper can be
          broken on purpose. Flip its page layout and the fourth collector goes blind.
          One API call then asks Bright Data to read the new page and rewrite the
          collector by itself, and every stage streams into the run feed. Its products
          are invented and never matched against the real apps.
        </p>

        <p className="mono mt-6 border-t pt-4 text-[12px] text-[var(--text-dim)]" style={{ borderColor: "var(--hair)" }}>
          Watch a demo replays a real capture and costs nothing.{" "}
          <a
            href="https://youtu.be/lyB69pgtWZ8"
            target="_blank"
            rel="noreferrer noopener"
            className="link"
          >
            watch the 3 minute video
          </a>{" "}
          ·{" "}
          <a
            href="https://github.com/Prajwalsrinvas/ek-daam"
            target="_blank"
            rel="noreferrer noopener"
            className="link"
          >
            code on GitHub
          </a>
        </p>
      </div>
    </div>
  );
}
