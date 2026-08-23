/** Full-width, directly under band 0, and persistent while they apply. Each one
 *  states a fact about what the reader is looking at; none of them is a toast. */

function Banner({
  tone,
  label,
  children,
}: {
  tone: "warn" | "bad" | "dim";
  label: string;
  children: React.ReactNode;
}) {
  const color = tone === "warn" ? "var(--warn)" : tone === "bad" ? "var(--bad)" : "var(--text-dim)";
  return (
    <p
      className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2 text-[13px]"
      style={{ border: `1px solid ${color}`, color: "var(--text)" }}
      role={tone === "bad" ? "alert" : undefined}
    >
      <span className="kicker shrink-0" style={{ color }}>
        {label}
      </span>
      <span className="min-w-0">{children}</span>
    </p>
  );
}

export function ReplayBanner() {
  return (
    <Banner tone="warn" label="REPLAY">
      These events were captured earlier and are being re-streamed at the speed they happened at.
      Nothing here is live, and no collector is being paid for it.
    </Banner>
  );
}

export function MockBanner() {
  return (
    <Banner tone="dim" label="MOCK">
      BD_MODE=mock. Rows come from a committed fixture, not from a live collector run, and only one
      universe has one, so a mock run has nothing to compare.
    </Banner>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <Banner tone="bad" label="STOPPED">
      {message}
    </Banner>
  );
}
