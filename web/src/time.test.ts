// One clock, one zone, always labelled. Two timestamps on a receipt are only
// comparable if they are read the same way, and they were not: the event feed
// printed raw UTC while the comparison cells used the viewer's local time.

import { describe as group, expect, it } from "vitest";

import { istClock, istClockMs } from "./time";

group("istClock", () => {
  it("renders a UTC instant in IST, labelled", () => {
    // 09:12:33 UTC is 14:42:33 in IST (+05:30).
    expect(istClock("2026-08-22T09:12:33.412Z")).toBe("14:42:33 IST");
  });

  it("is independent of the viewer's own timezone", () => {
    // Same instant written with a different offset must render identically.
    expect(istClock("2026-08-22T09:12:33.412Z")).toBe(istClock("2026-08-22T04:12:33.412-05:00"));
  });

  it("keeps milliseconds where event ordering within a second matters", () => {
    expect(istClockMs("2026-08-22T09:12:33.412Z")).toBe("14:42:33.412 IST");
  });

  it("returns null rather than inventing a time", () => {
    expect(istClock(null)).toBeNull();
    expect(istClock("")).toBeNull();
    expect(istClock("not a timestamp")).toBeNull();
    expect(istClockMs(undefined)).toBeNull();
  });
});
