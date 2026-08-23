import { useCallback, useEffect, useMemo, useState } from "react";

import { getCarts, getRuns, getUniverses, startCart, startReplay, startRun } from "./api";
import { ErrorBanner, MockBanner, ReplayBanner } from "./components/Banners";
import BasketBar from "./components/BasketBar";
import CompareForm from "./components/CompareForm";
import DemoStrip from "./components/DemoStrip";
import FlightRecorder from "./components/FlightRecorder";
import MyRuns from "./components/MyRuns";
import PortalDeck from "./components/PortalDeck";
import Receipt from "./components/Receipt";
import TopBar, { type Tab } from "./components/TopBar";
import Guide from "./components/Guide";
import { emptyRunState } from "./runState";
import type { Cart, DemoEntry, RunMeta, Universe } from "./types";
import { EMPTY_COMPARISON, useRunStreams } from "./useRunStream";

/** What the page is currently showing: one run, or one run per cart item.
 *  `labels` is aligned to `runIds` and holds the item name for a cart, null for
 *  a single run. */
interface Viewing {
  runIds: string[];
  labels: (string | null)[];
  /** The demo card that started this, so the card can say REPLAYING. */
  demoId: string | null;
}

const NOTHING: Viewing = { runIds: [], labels: [], demoId: null };

/** A once-per-second tick, and only while something is actually running.
 *  Everything it feeds shows a REAL duration measured from a real event's
 *  timestamp; nothing in this app counts up towards an outcome it has not seen. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** `#demo/<id>` deep-links a demo card, which is how a capture gets shared. */
function demoIdInHash(): string | null {
  const hash = window.location.hash.replace(/^#/, "");
  return hash.startsWith("demo/") ? hash.slice("demo/".length) : null;
}

export default function App() {
  const [registry, setRegistry] = useState<Universe[]>([]);
  const [serverMode, setServerMode] = useState("");
  const [allowlist, setAllowlist] = useState<string[]>([]);
  const [demos, setDemos] = useState<DemoEntry[]>([]);
  const [tab, setTab] = useState<Tab>("demo");
  const [viewing, setViewing] = useState<Viewing>(NOTHING);
  const [myRuns, setMyRuns] = useState<RunMeta[]>([]);
  const [myCarts, setMyCarts] = useState<Cart[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [recorderOpen, setRecorderOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);

  const streams = useRunStreams(viewing.runIds);
  const lanes = useMemo(
    () =>
      viewing.runIds.map((runId, i) => ({
        runId,
        label: viewing.labels[i] ?? null,
        lane: streams[runId],
      })),
    [viewing, streams],
  );

  const inFlight = lanes.length > 0 && lanes.some(({ lane }) => !lane?.state.done);
  const now = useNow(inFlight);
  const first = lanes[0]?.lane ?? null;
  const isCart = lanes.length > 1;
  const anyReplay = lanes.some(({ lane }) => lane?.state.replay);
  const runError = lanes.find(({ lane }) => lane?.state.error)?.lane?.state.error ?? null;
  const isMock = (first?.state.mode ?? serverMode) === "mock";

  // Failing quietly is right here and only here: this listing is a convenience
  // panel, and one that could not be fetched must not put an error banner over a
  // run the visitor is watching.
  const refreshMine = useCallback(() => {
    getRuns()
      .then((data) => setMyRuns(data.runs))
      .catch(() => undefined);
    getCarts()
      .then((data) => setMyCarts(data.carts ?? []))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    getUniverses()
      .then((data) => {
        setRegistry(data.universes);
        setServerMode(data.mode);
        setAllowlist(data.query_allowlist ?? []);
        // A server that has not been taught the demo list still has the single
        // legacy capture, and it is exactly one demo entry.
        const list = data.demo ?? [];
        setDemos(
          list.length > 0
            ? list
            : data.demo_run_id
              ? [
                  {
                    id: "demo",
                    title: "The captured demo run",
                    note: "one product at 560001, re-streamed at the speed it happened at",
                    kind: "run" as const,
                    run_ids: [data.demo_run_id],
                    items: null,
                    chapters: null,
                  },
                ]
              : [],
        );
      })
      .catch((e: Error) => setError(e.message));
    refreshMine();
  }, [refreshMine]);

  // A run only joins the listing once it is stored as done, so the listing is
  // re-read when one ends rather than when one starts.
  useEffect(() => {
    if (!inFlight && viewing.runIds.length > 0) refreshMine();
  }, [inFlight, viewing.runIds.length, refreshMine]);

  /** Start one replay per run id and show whatever actually started. A cart
   *  whose fourth replay was refused shows three items and says the fourth was
   *  refused; it never shows a basket that quietly lost a product. */
  const replayAll = useCallback(
    async (runIds: string[], labels: (string | null)[], demoId: string | null) => {
      setStarting(true);
      setError(null);
      try {
        const results = await Promise.allSettled(runIds.map((id) => startReplay(id)));
        const started: string[] = [];
        const startedLabels: (string | null)[] = [];
        const refused: string[] = [];
        results.forEach((result, i) => {
          if (result.status === "fulfilled") {
            started.push(result.value.run_id);
            startedLabels.push(labels[i] ?? null);
          } else {
            refused.push((result.reason as Error).message);
          }
        });
        if (started.length === 0) {
          setError(refused[0] ?? "the replay could not be started");
          return;
        }
        if (refused.length > 0) {
          setError(
            `${refused.length} of ${runIds.length} could not be replayed: ${refused[0]}. ` +
              "What is shown below is the rest.",
          );
        }
        setViewing({ runIds: started, labels: startedLabels, demoId });
      } finally {
        setStarting(false);
      }
    },
    [],
  );

  const playDemo = useCallback(
    (entry: DemoEntry) => {
      window.location.hash = `demo/${entry.id}`;
      const labels = entry.run_ids.map(
        (_, i) => entry.items?.[i] ?? entry.chapters?.[i] ?? null,
      );
      void replayAll(entry.run_ids, labels, entry.id);
    },
    [replayAll],
  );

  // A shared link opens the capture it names, once, when the list arrives.
  const [linkHandled, setLinkHandled] = useState(false);
  useEffect(() => {
    if (linkHandled || demos.length === 0) return;
    setLinkHandled(true);
    const wanted = demoIdInHash();
    const entry = wanted ? demos.find((d) => d.id === wanted) : undefined;
    if (entry) playDemo(entry);
  }, [demos, linkHandled, playDemo]);

  const compare = useCallback(async (items: string[], pincode: string) => {
    setStarting(true);
    setError(null);
    window.location.hash = "";
    try {
      if (items.length === 1) {
        const { run_id } = await startRun(items[0], pincode);
        setViewing({ runIds: [run_id], labels: [null], demoId: null });
      } else {
        const cart = await startCart(items, pincode);
        setViewing({
          runIds: cart.items.map((item) => item.run_id),
          labels: cart.items.map((item) => item.item),
          demoId: null,
        });
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStarting(false);
    }
  }, []);

  const busy = starting || inFlight;

  return (
    <div className="min-h-full">
      <div className="mx-auto max-w-[1440px] px-6 pb-16 lg:pr-16">
        <TopBar tab={tab} onTab={setTab} onGuide={() => setGuideOpen(true)} mode={first?.state.mode ?? serverMode} pincode={first?.state.pincode ?? null} replay={anyReplay} />
        <Guide open={guideOpen} onClose={() => setGuideOpen(false)} />

        <div className="space-y-3 pb-5">
          {tab === "demo" ? (
            <DemoStrip
              entries={demos}
              onPlay={playDemo}
              busy={busy}
              playingId={viewing.demoId}
            />
          ) : (
            <>
              <CompareForm
                onCompare={compare}
                busy={starting}
                inFlight={inFlight}
                allowlist={allowlist}
              />
              <MyRuns
                runs={myRuns}
                carts={myCarts}
                onReplayRun={(runId) => void replayAll([runId], [null], null)}
                onReplayCart={(cart) =>
                  void replayAll(
                    cart.items.map((item) => item.run_id),
                    cart.items.map((item) => item.item),
                    null,
                  )
                }
                busy={busy}
                showing={viewing.runIds}
              />
            </>
          )}
        </div>

        {(anyReplay || isMock || error || runError) && (
          <div className="space-y-2 pb-5">
            {anyReplay && <ReplayBanner />}
            {isMock && <MockBanner />}
            {error && <ErrorBanner message={error} />}
            {runError && <ErrorBanner message={`The run stopped: ${runError}`} />}
          </div>
        )}

        {isCart && (
          <div className="pb-4">
            <BasketBar
              items={lanes.map(({ label, lane }, i) => ({
                label: label ?? `product ${i + 1}`,
                comparison: lane?.comparison ?? EMPTY_COMPARISON,
              }))}
              registry={registry}
            />
          </div>
        )}

        {lanes.length === 0 ? (
          <div className="space-y-3">
            <PortalDeck
              registry={registry}
              state={emptyRunState()}
              comparison={EMPTY_COMPARISON}
              landing
              now={now}
            />
            <Receipt
              comparison={EMPTY_COMPARISON}
              registry={registry}
              query=""
              pincode={null}
              llm={null}
              landing
              done={false}
              now={now}
            />
          </div>
        ) : (
          <div className="space-y-8">
            {lanes.map(({ runId, label, lane }) => {
              const state = lane?.state ?? emptyRunState();
              const comparison = lane?.comparison ?? EMPTY_COMPARISON;
              return (
                <div key={runId} className="space-y-3">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <h2 className="kicker">
                      {label ?? state.query ?? "loading"}
                      {state.pincode ? ` · ${state.pincode}` : ""}
                    </h2>
                    <span className="mono text-[11px] text-[var(--text-dim)]">
                      {runId} · {state.done ? (state.error ? "stopped" : "complete") : "in flight"}
                    </span>
                  </div>
                  <PortalDeck
                    registry={registry}
                    state={state}
                    comparison={comparison}
                    landing={false}
                    now={now}
                  />
                  {lane?.comparisonError && <ErrorBanner message={lane.comparisonError} />}
                  <Receipt
                    comparison={comparison}
                    registry={registry}
                    query={label ?? state.query ?? ""}
                    pincode={state.pincode}
                    llm={state.llm}
                    landing={false}
                    done={state.done}
                    now={now}
                  />
                </div>
              );
            })}
          </div>
        )}

        <footer
          className="mono mt-10 flex flex-wrap items-center justify-between gap-3 pt-4 text-[11px] text-[var(--text-dim)]"
          style={{ borderTop: "1px solid var(--hair)" }}
        >
          <span>EkDaam · shelf prices read at one pincode, nothing invented</span>
          <span className="flex flex-wrap items-center gap-4">
            <a
              href="https://github.com/Prajwalsrinvas/ek-daam"
              target="_blank"
              rel="noreferrer noopener"
              className="link"
            >
              code on GitHub
            </a>
            <a href="/chaos" target="_blank" rel="noreferrer noopener" className="link">
              open the demo store this app breaks and repairs on purpose
            </a>
          </span>
        </footer>
      </div>

      <FlightRecorder
        lanes={lanes.map(({ runId, label, lane }) => ({
          runId,
          label,
          state: lane?.state ?? emptyRunState(),
        }))}
        registry={registry}
        open={recorderOpen}
        onOpenChange={setRecorderOpen}
      />
    </div>
  );
}
