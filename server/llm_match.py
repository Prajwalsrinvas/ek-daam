"""The model-assisted matching pass: additional, labelled, never the receipt.

The deterministic resolver in `server/resolve.py` is not touched by anything
here. This runs afterwards, on the same captured rows, and hands back groups of
its own that travel beside the receipt as `Comparison.llm_groups` with
`source="model"`. A shopper can therefore always tell which rows were put
together by rules they can read and which were put together by a model.

WHY A MODEL AT ALL. The one match that matters most on the reference run is
`Amul Pasteurised Butter` at one shop against `Amul Salted Butter` at another:
the same 500 g pack of the same butter under two listing names. No amount of
token overlap finds that, and embeddings rank it BELOW `Amul Salted` against
`Amul Unsalted`, which is a different product. It needs the knowledge that
Amul's "Pasteurised Butter" is its salted butter. That is lexical knowledge, so
it is asked of something that has some. Measured in spike/llm-match: the
embedding sweep never separated the true match from the false merge at any
threshold, on any model.

WHAT THE MODEL IS ALLOWED TO SAY. Ids, and nothing else. Names, prices, pack
sizes and images are joined back locally from the rows the collectors captured,
so the model cannot invent a price, a product or a shop. Everything it returns
then passes deterministic guards again (`_admit`), and a group that fails one
is rejected rather than trusted.

WHAT IT IS SENT. Rows are blocked by brand token, base pack size and base unit
using `server/resolve.py`'s own helpers, and only blocks that span two or more
real shops are sent at all: a block confined to one shop cannot produce a
cross-shop match, so paying for it would buy nothing. One call per block, in
parallel, which makes "never put two pack sizes in one group" structurally
impossible rather than a request in a prompt. Demo universes never leave the
process: `chaos` prices are invented by this app, and a model reasoning over
them would be reasoning about fiction.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .events import EventStore, EventType
from .resolve import (
    DEMO_UNIVERSES,
    Comparison,
    ComparisonGroup,
    LlmSummary,
    NormalizedRow,
    brand_token,
    to_base_qty,
)

log = logging.getLogger("scrapeverse.llm_match")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Measured in spike/llm-match/bench_latency.py: the default reasoning effort
# costs 7.3s / 16.9s on these jobs and answers the same, `low` costs 3.8s / 2.9s.
# ox-alpha rejects `enabled: false` outright ("Reasoning is mandatory for this
# endpoint"), so the effort is turned down rather than off.
REASONING = {"effort": "low"}
MAX_TOKENS = 8000
MAX_RETRIES = 2

# Both verified on this job at comparable latency, for the day ox-alpha (a
# stealth model) is withdrawn or rate limited. Tried in order, and only on a 429
# or a 5xx: a bad request would fail the same way on all three.
FALLBACK_MODELS = (
    "nvidia/nemotron-3-super-120b-a12b:free",
    "dots-studio/dots-3-note-preview:free",
)

# Blocks in flight at once. The wall clock of the phase is the slowest block, not
# the sum, and 15 blocks at 8 concurrent was what the spike measured at 8s.
MAX_CONCURRENT_CALLS = 8

SYSTEM_PROMPT = (
    "You group product listings scraped from different Indian quick-commerce "
    "apps (Blinkit, Zepto, Swiggy Instamart) for one search.\n"
    "The input is already split into BLOCKS. Every listing inside a block shares "
    "a brand and an identical pack size.\n"
    "Within each block only, find listings that are THE SAME PRODUCT sold under "
    "different listing names.\n"
    "Rules:\n"
    "- NEVER put ids from two different blocks in one group.\n"
    "- Only group ids from DIFFERENT shops. Two listings in one shop are two "
    "products, not one.\n"
    "- Salted and unsalted are DIFFERENT products. So are butter and margarine, "
    "butter and cheese, butter and paneer, a block of butter and butter chiplets, "
    "white bread and whole wheat bread.\n"
    "- Different words for one product ARE the same product - for example a "
    "manufacturer's own trade name for its standard salted butter.\n"
    "- Leave a listing out when you are not sure. A missing group is fine; a "
    "wrong group is not."
)


class CompactCluster(BaseModel):
    """The whole contract with the model: which ids belong together, how sure.

    Deliberately without a canonical name or a reason. Both are derivable from
    rows already held, both cost about 50 output tokens per group, and on a
    single call that prose is wall clock not spent finding matches: the spike
    measured the verbose schema at 37s against 26s for this one, finding 14
    groups instead of 19.
    """

    same: list[list[str]] = Field(
        default_factory=list,
        description=(
            "Groups of ids that are clearly the same product under different "
            "listing names."
        ),
    )
    maybe: list[list[str]] = Field(
        default_factory=list,
        description="Groups you believe are the same product but would not stake a claim on.",
    )


@dataclass(frozen=True)
class LlmOutcome:
    """What the phase produced: the summary for the snapshot and the groups.

    `groups` is already stripped of anything the receipt found on its own, so it
    is exactly what the model added. `summary.accepted` still counts every group
    that cleared the guards, including those duplicates.
    """

    summary: LlmSummary
    groups: list[ComparisonGroup] = field(default_factory=list)


# -- what the model is shown --------------------------------------------------
def real_rows(rows_by_universe: dict[str, list[NormalizedRow]]) -> list[NormalizedRow]:
    """Every row a shop really served, in a stable order.

    `DEMO_UNIVERSES` are dropped here rather than filtered later, for the same
    reason `match()` drops them before bucketing: a demo row that reached the
    prompt could be grouped with three real ones, and this app's own invented
    price would end up in a row presented as a price comparison.
    """
    return [
        row
        for universe in sorted(rows_by_universe)
        if universe not in DEMO_UNIVERSES
        for row in rows_by_universe[universe]
    ]


def assign_ids(rows: Iterable[NormalizedRow]) -> list[tuple[str, NormalizedRow]]:
    """`b0 z3 i12`: the first letter of the shop plus a counter, per row.

    Every id is spent twice, once in the prompt and once in the answer, so they
    are as short as they can be while staying readable in a rejection message.
    The counter is per first letter, so two shops whose names start alike still
    get distinct ids.
    """
    counters: dict[str, int] = {}
    out: list[tuple[str, NormalizedRow]] = []
    for row in rows:
        head = (row.universe or "?")[0]
        index = counters.get(head, 0)
        counters[head] = index + 1
        out.append((f"{head}{index}", row))
    return out


def block_key(row: NormalizedRow) -> str:
    """Brand token, base pack size, base unit: the bucket a row may be matched in.

    The same shape `resolve.group_key` uses, minus the variant, because deciding
    whether "pasteurised" and "salted" are one variant is the question being
    asked. A row whose pack size did not parse carries its universe in the key,
    which makes it structurally impossible to group across shops.
    """
    qty, unit = to_base_qty(row.qty, row.unit)
    token = brand_token(row.brand, row.name)
    if qty is None or unit is None:
        return f"{token}|?|?|{row.universe}"
    return f"{token}|{qty:g}|{unit}"


def candidate_blocks(
    pairs: list[tuple[str, NormalizedRow]],
) -> dict[str, list[tuple[str, NormalizedRow]]]:
    """The blocks worth asking about, in key order.

    A block whose rows all come from one shop cannot produce a cross-shop match
    however good the model is, so it is never sent. On the reference run that is
    what takes 128 rows in 40 blocks down to 74 rows in 15.
    """
    blocks: dict[str, list[tuple[str, NormalizedRow]]] = {}
    for sku, row in pairs:
        blocks.setdefault(block_key(row), []).append((sku, row))
    return {
        key: members
        for key, members in sorted(blocks.items())
        if len({row.universe for _, row in members}) >= 2
    }


def render_block(key: str, members: list[tuple[str, NormalizedRow]]) -> str:
    """One block as the model sees it: a heading and one line per row.

    Id, name, shop, price. No image urls, store ids, timestamps or ratings:
    none of them says whether two listings are the same product, and every one
    of them would be tokens spent to say nothing.
    """
    lines = [f"## {key}"]
    for sku, row in sorted(members, key=lambda m: (m[1].universe, m[1].name)):
        price = f"  Rs{row.price:g}" if row.price else ""
        lines.append(f"{sku}  {row.name}  [{row.universe}]{price}")
    return "\n".join(lines)


# -- the call -----------------------------------------------------------------
@contextlib.asynccontextmanager
async def _llm_client(settings: Settings) -> AsyncIterator[Any]:
    """One instructor client for the whole phase, closed when it ends.

    Imported here rather than at module scope because the run manager imports
    this module on every boot while the layer itself is off unless a key is
    configured, and `openai` costs half a second to import.
    """
    import instructor
    from openai import AsyncOpenAI

    raw = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=settings.openrouter_api_key)
    try:
        yield instructor.from_openai(raw, mode=instructor.Mode.JSON)
    finally:
        with contextlib.suppress(Exception):
            await raw.close()


def _is_upstream_overload(exc: BaseException) -> bool:
    """A 429 or a 5xx from OpenRouter, wherever in the chain it ended up.

    Looked for by status rather than caught by type: instructor raises an error
    of its own once its retries are spent and carries the provider's exception
    as the cause, so the status is a link or two down.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        if status == 429 or (isinstance(status, int) and 500 <= status < 600):
            return True
        current = current.__cause__ or current.__context__
    return False


async def _ask_block(client: Any, prompt: str, model: str) -> tuple[CompactCluster, str]:
    """One block, one structured call. Returns the answer and who answered.

    THE SEAM. This is the only function in the module that reaches the network,
    which is what lets every test above it run offline against a stub.

    The fallback models are tried only on a 429 or a 5xx, because those are the
    failures that are about the provider rather than about the request.
    """
    last: BaseException | None = None
    for candidate in (model, *FALLBACK_MODELS):
        try:
            cluster, _ = await client.chat.completions.create_with_completion(
                model=candidate,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_model=CompactCluster,
                max_tokens=MAX_TOKENS,
                max_retries=MAX_RETRIES,
                extra_body={"reasoning": REASONING},
            )
            return cluster, candidate
        except Exception as exc:
            if not _is_upstream_overload(exc):
                raise
            log.warning("llm_match: %s is overloaded, falling back", candidate)
            last = exc
    raise last if last is not None else RuntimeError("no model was asked")


# -- the guards ---------------------------------------------------------------
def _admit(
    ids: list[str],
    block: str,
    by_id: dict[str, NormalizedRow],
    used: set[str],
) -> str | None:
    """Why this group must be refused, or None if it may stand.

    Deterministic checks, re-applied to every group the model returns. They are
    the reason a wrong answer costs nothing: in the spike's one-call arm they
    caught a real error, a group holding two 200 g rows and one 500 g row.

    `block` is the block this answer came back for, so a group is checked not
    only for internal agreement but against the question it was asked.
    """
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        # An id nobody was given. Either invented, or from a block that was never
        # sent, and both mean the group is about rows the model did not see.
        return f"unknown id(s) {unknown}"
    if any(i in used for i in ids):
        return "id already used in another group"
    keys = {block_key(by_id[i]) for i in ids}
    if len(keys) > 1:
        return "crosses a block: two brands or two pack sizes"
    if keys != {block}:
        return f"answers about {sorted(keys)}, which is not the block it was sent"
    if len({by_id[i].universe for i in ids}) < 2:
        return "one shop only, which is not a comparison"
    return None


def _signature(rows: Iterable[NormalizedRow]) -> frozenset[tuple[str, str | None]]:
    """A group's identity: which listing in which shop. Used to tell a model
    group that merely reproduces a rules group from one that adds something."""
    return frozenset((row.universe, row.product_id) for row in rows)


def _group_for(
    ids: list[str], by_id: dict[str, NormalizedRow], confidence: str, index: int
) -> ComparisonGroup:
    """An accepted group as the UI receives it: rows joined back from the capture.

    Brand, pack size and unit are read off the rows, never off the answer. The
    key carries `#m<n>` so a model group can never collide with the rules group
    for the same block, and so two model groups inside one block stay distinct.
    """
    rows = sorted((by_id[i] for i in ids), key=lambda r: (r.universe, r.name))
    head = rows[0]
    base_qty, base_unit = to_base_qty(head.qty, head.unit)
    return ComparisonGroup(
        key=f"{block_key(head)}#m{index}",
        brand=head.brand,
        qty=base_qty if base_qty is not None else head.qty,
        unit=base_unit or head.unit,
        variant=head.variant,
        confidence=confidence,  # type: ignore[arg-type]
        source="model",
        universes=sorted({row.universe for row in rows}),
        rows=rows,
    )


def collect_groups(
    answers: list[tuple[str, CompactCluster]],
    by_id: dict[str, NormalizedRow],
    rules_signatures: set[frozenset[tuple[str, str | None]]],
) -> tuple[list[ComparisonGroup], int, int]:
    """Guard every answer, then drop what the receipt already had.

    Returns (groups the model ADDS, accepted, rejected). The two counts describe
    what the model got right; the list is what is worth showing, so a group the
    receipt already made is counted and then dropped.
    """
    used: set[str] = set()
    accepted = 0
    rejected = 0
    per_block: dict[str, int] = {}
    added: list[ComparisonGroup] = []

    for key, cluster in answers:
        for confidence, raw_groups in (("high", cluster.same), ("low", cluster.maybe)):
            for raw in raw_groups:
                ids = list(dict.fromkeys(raw))
                refusal = _admit(ids, key, by_id, used)
                if refusal is not None:
                    rejected += 1
                    log.info("llm_match: rejected %s from block %s: %s", ids, key, refusal)
                    continue
                used.update(ids)
                accepted += 1
                index = per_block.get(key, 0)
                per_block[key] = index + 1
                group = _group_for(ids, by_id, confidence, index)
                if _signature(group.rows) in rules_signatures:
                    # The receipt found this one too. Counted as accepted (the
                    # model was right) and left out of the layer, which is only
                    # ever what the receipt could not do.
                    continue
                added.append(group)

    return added, accepted, rejected


# -- the phase ----------------------------------------------------------------
async def _report(store: EventStore, data: dict[str, Any]) -> None:
    await store.append(EventType.LLM_MATCH, data)


async def _skipped(store: EventStore, reason: str) -> LlmOutcome:
    await _report(store, {"status": "skipped", "reason": reason})
    return LlmOutcome(summary=LlmSummary(status="skipped", reason=reason))


async def run_llm_match(
    store: EventStore,
    settings: Settings,
    rows_by_universe: dict[str, list[NormalizedRow]],
    comparison: Comparison,
) -> LlmOutcome:
    """Ask the model about this run's rows, guard the answer, report both.

    TOTAL BY CONSTRUCTION. Every path returns an outcome and every failure is a
    `failed` status rather than an exception: this layer is an addition to a run
    that has already produced its receipt, so nothing it can do may take that
    run down.

    The wall budget covers the phase, not a call. Blocks still in flight when it
    expires are cancelled and dropped, and the phase reports `done` with what did
    come back - a partial answer to an optional question is worth more than
    nothing at all. Only a phase where NOTHING came back is a failure.
    """
    if not settings.llm_enabled:
        return await _skipped(store, "disabled")
    if not settings.openrouter_api_key:
        return await _skipped(store, "no key")

    pairs = assign_ids(real_rows(rows_by_universe))
    blocks = candidate_blocks(pairs)
    if not blocks:
        # Either fewer than two shops delivered rows, or no two of them stock the
        # same brand at the same pack size. Nothing to ask about either way.
        return await _skipped(store, "nothing to compare")

    by_id = {sku: row for members in blocks.values() for sku, row in members}
    rows_sent = len(by_id)
    await _report(
        store,
        {
            "status": "started",
            "model": settings.llm_model,
            "blocks": len(blocks),
            "rows_sent": rows_sent,
        },
    )

    started = time.monotonic()
    try:
        answers, models, failures = await _ask_all(settings, blocks)
        seconds = round(time.monotonic() - started, 1)
        if not answers:
            reason = (
                failures[0]
                if failures
                else f"no block answered within {settings.llm_timeout_s:g}s"
            )
            return await _failed(store, reason, seconds, len(blocks), rows_sent)
        rules_signatures = {_signature(group.rows) for group in comparison.groups}
        groups, accepted, rejected = collect_groups(answers, by_id, rules_signatures)
    except Exception as exc:
        # The whole phase, not only the calls: a run that has already produced a
        # receipt must not be taken down by anything this layer does, including
        # a bug in the guards.
        seconds = round(time.monotonic() - started, 1)
        return await _failed(
            store, _terse(exc, settings.openrouter_api_key), seconds, len(blocks), rows_sent
        )

    # Normally one model answered everything. Naming all of them keeps the
    # report true on the run where some blocks fell back and some did not.
    model = ", ".join(sorted(models))
    await _report(
        store,
        {
            "status": "done",
            "accepted": accepted,
            "rejected": rejected,
            "seconds": seconds,
            "model": model,
        },
    )
    return LlmOutcome(
        summary=LlmSummary(
            status="done",
            model=model,
            seconds=seconds,
            blocks=len(blocks),
            rows_sent=rows_sent,
            accepted=accepted,
            rejected=rejected,
        ),
        groups=groups,
    )


async def _ask_all(
    settings: Settings,
    blocks: dict[str, list[tuple[str, NormalizedRow]]],
) -> tuple[list[tuple[str, CompactCluster]], set[str], list[str]]:
    """Every block at once, inside one wall budget. Returns (answers in block
    order, the models that answered, the failures worth reporting).

    `asyncio.wait` rather than `wait_for` around a gather: a timeout has to leave
    the blocks that already answered in hand, and cancelling a gather throws
    those away with the rest.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    async with _llm_client(settings) as client:

        async def ask(key: str, prompt: str) -> tuple[str, CompactCluster, str]:
            async with semaphore:
                cluster, model = await _ask_block(client, prompt, settings.llm_model)
                return key, cluster, model

        tasks = [
            asyncio.create_task(ask(key, render_block(key, members)))
            for key, members in blocks.items()
        ]
        try:
            done, _ = await asyncio.wait(tasks, timeout=settings.llm_timeout_s)
        finally:
            # On the deadline AND on a shutdown that cancels the phase: a task
            # left running here would outlive the client it is calling through.
            pending = [task for task in tasks if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    answers: list[tuple[str, CompactCluster]] = []
    models: set[str] = set()
    failures: list[str] = []
    for task in tasks:
        if task not in done:
            continue
        error = task.exception()
        if error is not None:
            failures.append(_terse(error, settings.openrouter_api_key))
            log.warning("llm_match: a block failed: %s", failures[-1])
            continue
        key, cluster, model = task.result()
        answers.append((key, cluster))
        models.add(model)

    # Block order, so the "id already used" guard resolves the same way twice.
    answers.sort(key=lambda answer: answer[0])
    return answers, models, failures


async def _failed(
    store: EventStore, reason: str, seconds: float, blocks: int, rows_sent: int
) -> LlmOutcome:
    await _report(store, {"status": "failed", "reason": reason, "seconds": seconds})
    return LlmOutcome(
        summary=LlmSummary(
            status="failed",
            model=None,
            seconds=seconds,
            blocks=blocks,
            rows_sent=rows_sent,
            reason=reason,
        )
    )


def _terse(exc: BaseException, secret: str = "", limit: int = 160) -> str:
    """One short line, shown to the user and written to the run's event file.

    The key is removed rather than trusted not to be there: this text comes from
    a provider's error body, which is the one place in the app where a string we
    did not write could quote a request we sent.
    """
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    if secret:
        text = text.replace(secret, "***")
    return text[:limit]
