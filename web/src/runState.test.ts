// The pure core only. `foldEvent` is the whole UI's state machine, so it is the
// one piece of the frontend worth testing without a DOM.

import { describe as group, expect, it } from "vitest";

import {
  describe as describeEvent,
  emptyRunState,
  foldAll,
  foldEvent,
  IMPLEMENTED_EVENT_TYPES,
  isSettled,
  MAX_FEED,
  type RunState,
} from "./runState";
import type { EventType, RunEvent } from "./types";

let nextIndex = 1;

function event(
  type: EventType,
  data: Record<string, unknown> = {},
  extra: Partial<RunEvent> = {},
): RunEvent {
  return {
    i: nextIndex++,
    ts: "2026-08-22T09:12:33.412Z",
    run_id: "r_20260822_091230_ab12",
    universe: "zepto",
    type,
    replay: false,
    data,
    ...extra,
  };
}

function fresh(): RunState {
  nextIndex = 1;
  return emptyRunState();
}

/** A whole successful run, in the order the server emits it. */
function lifecycle(): RunEvent[] {
  return [
    event("run_requested", {
      query: "amul butter",
      pincode: "560001",
      area_label: "North Bengaluru",
      mode: "mock",
    }, { universe: null }),
    event("universe_dispatched", { display: "Zepto-verse" }),
    event("triggered", { job_id: "j_mock_zepto_0001", version: "dev" }),
    event("progress", { pages_left: 2, pages: 3 }),
    event("progress", { pages_left: 1, pages: 3 }),
    event("rows", { n: 7 }),
    event("screenshot", { artifact: "zepto.png", url: "/a/zepto.png", placeholder: true }),
    event("validated", { rows_kept: 6, rows_dropped: 1, reasons: { no_price: 1 } }),
    event("done", { rows_total: 6, groups: 0 }, { universe: null }),
  ];
}

group("ordering and dedupe", () => {
  it("tracks the highest folded index", () => {
    const state = foldAll(fresh(), lifecycle());

    expect(state.lastEventId).toBe(9);
    expect(state.feed.map((e) => e.i)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("drops an event already folded, so a reconnect overlap cannot double-count", () => {
    const state = fresh();
    const rows = event("rows", { n: 7 });
    const once = foldEvent(state, rows);
    const twice = foldEvent(once, rows);

    expect(twice).toBe(once); // same object: nothing was recomputed
    expect(twice.feed).toHaveLength(1);
    expect(twice.universes.zepto.rows).toBe(7);
  });

  it("ignores an event older than the last one folded", () => {
    const after = foldAll(fresh(), lifecycle());
    const stale = { ...lifecycle()[5], i: 3 };

    expect(foldEvent(after, stale)).toBe(after);
  });

  it("caps the feed but keeps the newest events", () => {
    nextIndex = 1;
    const many = Array.from({ length: MAX_FEED + 25 }, () => event("progress", { pages_left: 1 }));
    const state = foldAll(emptyRunState(), many);

    expect(state.feed).toHaveLength(MAX_FEED);
    expect(state.feed.at(-1)?.i).toBe(MAX_FEED + 25);
    expect(state.lastEventId).toBe(MAX_FEED + 25);
  });
});

group("state transitions", () => {
  it("reads the run header off run_requested", () => {
    const state = foldAll(fresh(), lifecycle().slice(0, 1));

    expect(state.query).toBe("amul butter");
    expect(state.pincode).toBe("560001");
    expect(state.areaLabel).toBe("North Bengaluru");
    expect(state.mode).toBe("mock");
    expect(state.runId).toBe("r_20260822_091230_ab12");
  });

  it("registers a universe on first sight and keeps dispatch order", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("universe_dispatched", {}, { universe: "zepto" }),
      event("universe_dispatched", {}, { universe: "blinkit" }),
      event("rows", { n: 1 }, { universe: "zepto" }),
    ]);

    expect(state.order).toEqual(["zepto", "blinkit"]);
    expect(Object.keys(state.universes).sort()).toEqual(["blinkit", "zepto"]);
  });

  it("walks a healthy universe to validated", () => {
    const state = foldAll(fresh(), lifecycle());
    const zepto = state.universes.zepto;

    expect(zepto.status).toBe("validated");
    expect(zepto.jobId).toBe("j_mock_zepto_0001");
    expect(zepto.rows).toBe(7);
    expect(zepto.rowsKept).toBe(6);
    expect(zepto.rowsDropped).toBe(1);
    expect(zepto.dropReasons).toEqual({ no_price: 1 });
    expect(zepto.screenshot).toEqual({
      artifact: "zepto.png",
      url: "/a/zepto.png",
      placeholder: true,
    });
    expect(zepto.lastEventAt).toBe("2026-08-22T09:12:33.412Z");
    expect(isSettled(zepto)).toBe(true);
  });

  it("shows pages remaining while collecting, then clears it on rows", () => {
    const collecting = foldAll(fresh(), lifecycle().slice(0, 5));
    expect(collecting.universes.zepto.status).toBe("collecting");
    expect(collecting.universes.zepto.pagesLeft).toBe(1);

    const parsed = foldAll(fresh(), lifecycle().slice(0, 6));
    expect(parsed.universes.zepto.status).toBe("rows");
    expect(parsed.universes.zepto.pagesLeft).toBe(0);
  });

  it("records zero_rows with its reason", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [event("zero_rows", { reason: "oos" })]);

    expect(state.universes.zepto.status).toBe("zero_rows");
    expect(state.universes.zepto.zeroReason).toBe("oos");
    expect(isSettled(state.universes.zepto)).toBe(true);
  });

  it("records failed with its error", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("failed", { error: "location proof failed: store_id mismatch" }),
    ]);

    expect(state.universes.zepto.status).toBe("failed");
    expect(state.universes.zepto.error).toBe("location proof failed: store_id mismatch");
  });

  it("records timed_out", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [event("timed_out", { after_s: 180 })]);

    expect(state.universes.zepto.status).toBe("timed_out");
    expect(isSettled(state.universes.zepto)).toBe(true);
  });

  it("marks the run done only on the run-level done event", () => {
    const before = foldAll(fresh(), lifecycle().slice(0, 8));
    const after = foldAll(fresh(), lifecycle());

    expect(before.done).toBe(false);
    expect(after.done).toBe(true);
  });

  it("ignores an unknown event type without losing the index", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [event("incident" as EventType, {})]);

    expect(state.lastEventId).toBe(1);
    expect(state.feed).toHaveLength(1);
    expect(state.universes.zepto.status).toBe("idle");
  });
});

group("artifact_failed is non-terminal", () => {
  it("records the error without touching status", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("rows", { n: 7 }),
      event("artifact_failed", { error: "RuntimeError: artifact host down" }),
      event("validated", { rows_kept: 7, rows_dropped: 0, reasons: {} }),
    ]);
    const zepto = state.universes.zepto;

    // A universe whose rows are fine must not read as failed.
    expect(zepto.status).toBe("validated");
    expect(zepto.error).toBeNull();
    expect(zepto.artifactError).toBe("RuntimeError: artifact host down");
    expect(zepto.screenshot).toBeNull();
  });

  it("does not settle a universe on its own", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("rows", { n: 7 }),
      event("artifact_failed", { error: "boom" }),
    ]);

    expect(state.universes.zepto.status).toBe("rows");
    expect(isSettled(state.universes.zepto)).toBe(false);
  });

  it("clears a stale artifact error when a capture does arrive", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("artifact_failed", { error: "boom" }),
      event("screenshot", { artifact: "zepto.png", url: "/a/zepto.png", placeholder: false }),
    ]);

    expect(state.universes.zepto.artifactError).toBeNull();
    expect(state.universes.zepto.screenshot?.artifact).toBe("zepto.png");
  });
});

group("replay", () => {
  it("latches the replay flag for the whole run once any event carries it", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("run_requested", {}, { universe: null, replay: true }),
      event("rows", { n: 7 }, { replay: true }),
    ]);

    expect(state.replay).toBe(true);
  });

  it("stays false for a live capture", () => {
    expect(foldAll(fresh(), lifecycle()).replay).toBe(false);
  });

  it("never un-latches, so a demo cannot lose its REPLAY banner mid-stream", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("rows", { n: 7 }, { replay: true }),
      event("validated", { rows_kept: 7, rows_dropped: 0, reasons: {} }, { replay: false }),
    ]);

    expect(state.replay).toBe(true);
  });
});

group("purity", () => {
  it("does not mutate the state it was handed", () => {
    const before = foldAll(fresh(), lifecycle().slice(0, 3));
    const snapshot = JSON.stringify(before);

    foldEvent(before, event("validated", { rows_kept: 6, rows_dropped: 1, reasons: {} }));

    expect(JSON.stringify(before)).toBe(snapshot);
  });

  it("is deterministic", () => {
    expect(JSON.stringify(foldAll(fresh(), lifecycle()))).toBe(
      JSON.stringify(foldAll(fresh(), lifecycle())),
    );
  });
});

group("describe", () => {
  it("renders a line for every implemented event type", () => {
    nextIndex = 1;
    for (const type of IMPLEMENTED_EVENT_TYPES) {
      const line = describeEvent(event(type, {}));
      expect(line, `no description for ${type}`).toBeTruthy();
    }
  });

  it("says the rows are unaffected when a capture fails", () => {
    nextIndex = 1;
    expect(describeEvent(event("artifact_failed", { error: "boom" }))).toContain("rows unaffected");
  });

  it("falls back to the bare type for a reserved event", () => {
    nextIndex = 1;
    expect(describeEvent(event("incident" as EventType, {}))).toBe("incident");
  });

  it("describes the self-heal cycle", () => {
    nextIndex = 1;
    expect(describeEvent(event("heal_started", { prompt_chars: 420 }))).toContain("420");
    expect(describeEvent(event("heal_approved", { auto_save: true }))).toContain("auto_save on");
    expect(describeEvent(event("heal_promoted", { template: "t_chaos.2" }))).toContain(
      "t_chaos.2",
    );
    expect(describeEvent(event("progress", { step: "code_fixer" }))).toBe("step code_fixer");
  });
});

group("a retriggered collector job", () => {
  it("is subscribed to and described", () => {
    // The array is the SSE subscription list: the server names every frame, and
    // a named frame absent from it never reaches the UI at all, silently.
    expect(IMPLEMENTED_EVENT_TYPES).toContain("retriggered");

    nextIndex = 1;
    const line = describeEvent(
      event("retriggered", {
        job_id: "j_stalled",
        after_s: 78.4,
        reason: "job never started navigating",
      }),
    );
    expect(line).toContain("never started");
    expect(line).toContain("78.4");
  });

  it("leaves the universe collecting rather than marking it failed", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("triggered", { job_id: "j_stalled", version: "prod" }),
      event("retriggered", { job_id: "j_stalled", after_s: 78.4 }),
      event("triggered", { job_id: "j_fresh", version: "prod" }),
    ]);
    const zepto = state.universes.zepto;

    // A universe being retried has not failed, and the job it is watching is
    // the replacement, not the one that was abandoned.
    expect(zepto.status).toBe("triggered");
    expect(isSettled(zepto)).toBe(false);
    expect(zepto.jobId).toBe("j_fresh");
    expect(zepto.error).toBeNull();
  });
});

group("a run-level failure ends the run", () => {
  it("settles the run and stores the reason when `failed` has no universe", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("run_requested", { query: "amul butter", pincode: "560001" }, { universe: null }),
      event("failed", { error: "BDError: trigger failed: HTTP 402" }, { universe: null }),
    ]);

    // The server emits this INSTEAD of `done`. Without settling, the UI waited
    // forever on a run that was already over: spinner up, no reason shown.
    expect(state.done).toBe(true);
    expect(state.error).toBe("BDError: trigger failed: HTTP 402");
  });

  it("settles the run on a run-level timed_out too", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("timed_out", { after_s: 240 }, { universe: null }),
    ]);

    expect(state.done).toBe(true);
    expect(state.error).toBe("the run timed out after 240s");
  });

  it("leaves a per-universe failure alone - the run carries on", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("failed", { error: "mapper blew up" }, { universe: "blinkit" }),
    ]);

    expect(state.done).toBe(false);
    expect(state.error).toBeNull();
    expect(state.universes.blinkit.status).toBe("failed");
  });

  it("tells the two apart in the feed line", () => {
    nextIndex = 1;
    expect(describeEvent(event("failed", { error: "boom" }, { universe: "zepto" }))).toBe(
      "failed: boom",
    );
    expect(describeEvent(event("failed", { error: "boom" }, { universe: null }))).toBe(
      "run failed: boom",
    );
  });

  it("still has no error on a healthy run", () => {
    expect(foldAll(fresh(), lifecycle()).error).toBeNull();
  });
});

group("the model matching layer", () => {
  it("is subscribed to, or the frontend never sees a single frame of it", () => {
    // The server names every SSE frame, and a named frame absent from this list
    // never reaches the UI at all, silently.
    expect(IMPLEMENTED_EVENT_TYPES).toContain("llm_match");
  });

  it("records what was asked, and when, from the started event", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event(
        "llm_match",
        { status: "started", model: "stealth/ox-alpha", blocks: 15, rows_sent: 74 },
        { universe: null },
      ),
    ]);

    expect(state.llm).toEqual({
      status: "started",
      model: "stealth/ox-alpha",
      blocks: 15,
      rowsSent: 74,
      accepted: null,
      rejected: null,
      seconds: null,
      reason: null,
      startedAt: "2026-08-22T09:12:33.412Z",
    });
  });

  it("keeps what `started` said when `done` arrives without it", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event(
        "llm_match",
        { status: "started", model: "stealth/ox-alpha", blocks: 15, rows_sent: 74 },
        { universe: null },
      ),
      event(
        "llm_match",
        { status: "done", accepted: 18, rejected: 0, seconds: 8.1 },
        { universe: null },
      ),
    ]);

    // The block and row counts are the size of the question that was asked, and
    // the answer event does not repeat them. Losing them would leave the receipt
    // saying "0 blocks" for a layer that really did send fifteen.
    expect(state.llm?.status).toBe("done");
    expect(state.llm?.blocks).toBe(15);
    expect(state.llm?.rowsSent).toBe(74);
    expect(state.llm?.model).toBe("stealth/ox-alpha");
    expect(state.llm?.accepted).toBe(18);
    expect(state.llm?.rejected).toBe(0);
    expect(state.llm?.seconds).toBe(8.1);
    // Still the moment the model was asked, which is what the elapsed counter
    // in the receipt is measured from.
    expect(state.llm?.startedAt).toBe("2026-08-22T09:12:33.412Z");
  });

  it("carries the reason on skipped and on failed", () => {
    nextIndex = 1;
    const skipped = foldAll(emptyRunState(), [
      event("llm_match", { status: "skipped", reason: "no key" }, { universe: null }),
    ]);
    expect(skipped.llm?.status).toBe("skipped");
    expect(skipped.llm?.reason).toBe("no key");
    expect(skipped.llm?.startedAt).toBeNull();

    nextIndex = 1;
    const failed = foldAll(emptyRunState(), [
      event(
        "llm_match",
        { status: "failed", reason: "every block failed", seconds: 30.0 },
        { universe: null },
      ),
    ]);
    expect(failed.llm?.status).toBe("failed");
    expect(failed.llm?.reason).toBe("every block failed");
    expect(failed.llm?.seconds).toBe(30);
  });

  it("is null on a run that never reported the layer", () => {
    // Every run captured before the layer existed. The receipt says "model: not
    // run" for these rather than inventing a status.
    expect(foldAll(fresh(), lifecycle()).llm).toBeNull();
  });

  it("touches no universe: the receipt above it is untouched", () => {
    const before = foldAll(fresh(), lifecycle());
    const after = foldEvent(
      before,
      event("llm_match", { status: "done", accepted: 3, rejected: 1 }, { universe: null }),
    );

    expect(after.universes).toEqual(before.universes);
    expect(after.order).toEqual(before.order);
    expect(after.done).toBe(before.done);
  });

  it("describes each status in its own words", () => {
    nextIndex = 1;
    expect(
      describeEvent(
        event(
          "llm_match",
          { status: "started", model: "stealth/ox-alpha", blocks: 15, rows_sent: 74 },
          { universe: null },
        ),
      ),
    ).toContain("15 block(s)");
    expect(
      describeEvent(
        event(
          "llm_match",
          { status: "done", model: "stealth/ox-alpha", accepted: 18, rejected: 2, seconds: 8.1 },
          { universe: null },
        ),
      ),
    ).toContain("2 rejected by the guards");
    expect(
      describeEvent(event("llm_match", { status: "skipped", reason: "replay" }, { universe: null })),
    ).toBe("model layer skipped: replay");
    expect(
      describeEvent(event("llm_match", { status: "failed", reason: "timeout" }, { universe: null })),
    ).toBe("model layer failed: timeout");
  });
});

group("the self-heal strip", () => {
  it("walks the four observed steps in order", () => {
    nextIndex = 1;
    const events = [
      event("heal_started", { prompt_chars: 420 }, { universe: "chaos" }),
      event(
        "heal_previewed",
        { step: "code_fixer", preview_result: [{ name: "a" }, { name: "b" }] },
        { universe: "chaos" },
      ),
      event("heal_approved", { auto_save: true }, { universe: "chaos" }),
      event("heal_promoted", { template: "healed_template" }, { universe: "chaos" }),
    ];

    expect(foldAll(emptyRunState(), events.slice(0, 1)).universes.chaos.heal?.step).toBe("plan");
    const previewed = foldAll(emptyRunState(), events.slice(0, 2)).universes.chaos.heal;
    expect(previewed?.step).toBe("preview");
    // The row count is what a reviewer approves on, so it is counted rather than
    // described.
    expect(previewed?.detail).toBe("paused at code_fixer, 2 preview row(s)");
    expect(foldAll(emptyRunState(), events.slice(0, 3)).universes.chaos.heal?.step).toBe("approve");
    const promoted = foldAll(emptyRunState(), events).universes.chaos.heal;
    expect(promoted?.step).toBe("promote");
    expect(promoted?.detail).toContain("healed_template");
  });

  it("never walks backwards, so a resumed stream cannot un-repair a collector", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("heal_promoted", { template: "t" }, { universe: "chaos" }),
      event("heal_started", { prompt_chars: 10 }, { universe: "chaos" }),
    ]);

    expect(state.universes.chaos.heal?.step).toBe("promote");
  });

  it("is null on an ordinary run", () => {
    expect(foldAll(fresh(), lifecycle()).universes.zepto.heal).toBeNull();
  });

  it("keeps the stage name a heal job reports through progress", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("progress", { step: "code_fixer" }, { universe: "chaos" }),
      event("progress", { step: "" }, { universe: "chaos" }),
    ]);

    // A later tick with no stage must not blank the last one that was named.
    expect(state.universes.chaos.stage).toBe("code_fixer");
    expect(state.universes.chaos.pagesLeft).toBeNull();
  });
});

group("a retriggered universe keeps its pill", () => {
  it("records how long the stalled job was watched, and keeps it to the end", () => {
    nextIndex = 1;
    const state = foldAll(emptyRunState(), [
      event("triggered", { job_id: "j_stalled", version: "prod" }),
      event("retriggered", { job_id: "j_stalled", after_s: 77.4 }),
      event("triggered", { job_id: "j_fresh", version: "prod" }),
      event("rows", { n: 22 }),
      event("validated", { rows_kept: 22, rows_dropped: 0, reasons: {} }),
    ]);
    const zepto = state.universes.zepto;

    // The second try is the most interesting thing that happened to this
    // universe, so it survives every later event rather than being replaced by
    // the happy ending.
    expect(zepto.retriggeredAfterS).toBe(77.4);
    expect(zepto.status).toBe("validated");
    expect(zepto.jobVersion).toBe("prod");
  });

  it("is null on a universe that never needed one", () => {
    expect(foldAll(fresh(), lifecycle()).universes.zepto.retriggeredAfterS).toBeNull();
  });
});
