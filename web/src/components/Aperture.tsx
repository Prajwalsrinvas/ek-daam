import type { UniverseStatus } from "../runState";

/** The portal aperture: a 56px ring, one drawing per observed state.
 *
 *  It carries NO information the words beside it do not also carry - the state
 *  word, the fact line and the row count are all in text - so a reader who
 *  cannot tell the drawings apart loses nothing. That is the whole reason it is
 *  allowed to be the one bold thing on the page.
 *
 *  The only animation is the collecting sweep, and it is a real one: it runs
 *  while, and only while, this universe has told us it is still collecting.
 *  Under `prefers-reduced-motion` the CSS drops the animation and the arc stands
 *  still at three quarters, which is a distinct drawing in its own right.
 */

const SIZE = 56;
const C = SIZE / 2;
const R = 22;
const CIRCUMFERENCE = 2 * Math.PI * R;

export type ApertureTone = "hue" | "ok" | "bad" | "warn" | "dim";

const TONE_VAR: Record<ApertureTone, string> = {
  hue: "var(--hue)",
  ok: "var(--ok)",
  bad: "var(--bad)",
  warn: "var(--warn)",
  dim: "var(--text-dim)",
};

export default function Aperture({
  status,
  tone,
  count,
}: {
  status: UniverseStatus;
  /** Which meaning this universe currently has. `hue` is the universe's own
   *  registry colour and means "working"; the rest are state colours. */
  tone: ApertureTone;
  /** Rows read, printed inside the disc once there are any. */
  count: number | null;
}) {
  const stroke = TONE_VAR[tone];
  const filled = status === "rows" || status === "validated";

  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      {/* The ring, in every state. What changes is how it is drawn. */}
      {status === "idle" && (
        <circle
          cx={C}
          cy={C}
          r={R}
          fill="none"
          stroke={stroke}
          strokeWidth={1.5}
          strokeDasharray="2 5"
          opacity={0.5}
        />
      )}

      {status === "dispatched" && (
        <circle
          cx={C}
          cy={C}
          r={R}
          fill="none"
          stroke={stroke}
          strokeWidth={1.5}
          strokeDasharray="2 5"
          opacity={0.95}
        />
      )}

      {status === "triggered" && (
        <circle cx={C} cy={C} r={R} fill="none" stroke={stroke} strokeWidth={2} />
      )}

      {status === "collecting" && (
        <>
          <circle cx={C} cy={C} r={R} fill="none" stroke={stroke} strokeWidth={2} opacity={0.22} />
          <circle
            className="aperture-sweep"
            cx={C}
            cy={C}
            r={R}
            fill="none"
            stroke={stroke}
            strokeWidth={2.5}
            strokeLinecap="butt"
            // Three quarters drawn, one quarter open. Rotating, it reads as a
            // sweep; stopped, it is a three-quarter arc.
            strokeDasharray={`${(CIRCUMFERENCE * 3) / 4} ${CIRCUMFERENCE / 4}`}
            transform={`rotate(-45 ${C} ${C})`}
          />
        </>
      )}

      {filled && (
        <>
          <circle
            cx={C}
            cy={C}
            r={R}
            fill={stroke}
            fillOpacity={status === "validated" ? 0.22 : 0.12}
            stroke={stroke}
            strokeWidth={status === "validated" ? 2 : 1.5}
          />
          {count !== null && (
            <text
              x={C}
              y={C}
              textAnchor="middle"
              dominantBaseline="central"
              fill="var(--text)"
              style={{
                fontFamily: "var(--font-display)",
                fontWeight: 700,
                fontSize: count > 99 ? 14 : 16,
                letterSpacing: "-0.02em",
              }}
            >
              {count}
            </text>
          )}
        </>
      )}

      {(status === "zero_rows" || status === "failed") && (
        <>
          <circle cx={C} cy={C} r={R} fill="none" stroke={stroke} strokeWidth={2} />
          {/* A strike, not a cross: one clean diagonal through a ring that did
              come back with something to say. */}
          <line
            x1={C - 13}
            y1={C - 13}
            x2={C + 13}
            y2={C + 13}
            stroke={stroke}
            strokeWidth={2}
          />
        </>
      )}

      {status === "timed_out" && (
        <>
          <circle cx={C} cy={C} r={R} fill="none" stroke={stroke} strokeWidth={2} />
          {/* A clock, stopped: the app gave up watching at a moment it can name. */}
          <line x1={C} y1={C} x2={C} y2={C - 11} stroke={stroke} strokeWidth={2} />
          <line x1={C} y1={C} x2={C + 8} y2={C + 5} stroke={stroke} strokeWidth={2} />
        </>
      )}
    </svg>
  );
}
