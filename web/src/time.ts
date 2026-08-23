// One clock for the whole UI.
//
// Every timestamp the app shows is part of a receipt: an event's moment, a row's
// capture time. Two of them are only comparable if they are read in the same
// zone, and they were not - the event feed printed raw UTC off the ISO string
// while the comparison cells used the viewer's local time, so the same instant
// appeared twice under two different clocks with nothing saying which was which.
//
// Everything the app scrapes is priced in India and captured in India, so IST is
// the zone the numbers mean something in, and the suffix is always shown. A
// viewer in another timezone reads one clock, labelled.

const IST = "Asia/Kolkata";

const CLOCK = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const CLOCK_WITH_MS = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hour12: false,
});

function parsed(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? null : at;
}

/** `14:22:07 IST`, or null when there is no usable timestamp. */
export function istClock(iso: string | null | undefined): string | null {
  const at = parsed(iso);
  return at === null ? null : `${CLOCK.format(at)} IST`;
}

/** `14:22:07.412 IST` - the event feed, where ordering within a second matters. */
export function istClockMs(iso: string | null | undefined): string | null {
  const at = parsed(iso);
  return at === null ? null : `${CLOCK_WITH_MS.format(at)} IST`;
}

/** Whole seconds between an event's timestamp and `now`, never negative.
 *  The UI shows elapsed time only where a real event started a real clock: a
 *  collector job that has been triggered, a model that has been asked. Nothing
 *  in this app counts up towards an outcome it has not observed. */
export function secondsSince(iso: string | null | undefined, now: number): number | null {
  const at = parsed(iso);
  if (at === null) return null;
  return Math.max(0, Math.floor((now - at.getTime()) / 1000));
}
