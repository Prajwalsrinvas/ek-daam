"""Bright Data self-healing, driven from the app.

Three REST calls, taken from the Scraper Studio AI Flow reference:

  POST /dca/collectors/{collector_id}/refactor_template
       body: {"prompt": "<= 1000 chars", "custom_input": [ {...}, ... ]}
       200 -> the self-healing job is running

  GET  /dca/collectors/{collector_id}/refactor_template/progress
       200 -> {"status": ..., "step": ..., "completed_steps": [...], ...}
       `status: "pending_answer"` with `step: "user_approval"` is the approval
       gate: the job has a proposed fix and is waiting to be told yes or no.

  POST /dca/collectors/{collector_id}/resume_automation_job
       body: {"message": true, "auto_save": true}
       200 -> the job resumes. `auto_save` is what publishes the healed template
       once the job succeeds; without it the approval leaves an unpublished
       draft and production keeps running the broken template.

Two things this module will not do:

  * It never invents progress. Every field on every event is lifted from a
    Bright Data response. A call that fails becomes a `failed` event carrying
    the status and body Bright Data returned.
  * It never reports a promotion it cannot see. The evidence that the healed
    template reached production is `save_new_template` appearing in the job's
    own `completed_steps`. A job that finishes without it is reported as a
    failure to publish, not as a heal.

The collector id travels in the URL and nowhere else: it is not put on any event,
exactly as in the rest of the app.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import Settings
from .events import EventStore, EventType

# The documented cap on a heal prompt.
PROMPT_MAX_LEN = 1000

# What the app asks for when the caller does not supply a prompt. It names the
# fields the collector must produce and says nothing about how to find them:
# handing the fix to the healer is the point of the exercise.
DEFAULT_PROMPT = (
    "The store page was redesigned and the scraper now returns no rows. "
    "Update the selectors so it again collects one row per product from the "
    "search results, with these fields: product_name, brand, package_size, "
    "product_id, mrp, selling_price, out_of_stock, available_quantity, "
    "is_sponsored, rating, image_url, store_id, requested_pincode, "
    "resolved_area, eta_minutes. resolved_area is the page's delivering-to line "
    "and must contain the requested pincode. Prices are shown in rupees with a "
    "currency symbol; collect the number only."
)

# The store the chaos collector reads, and the input it takes.
DEFAULT_CUSTOM_INPUT: list[dict[str, Any]] = [{"keyword": "amul butter", "pincode": "560001"}]

APPROVAL_STATUSES = frozenset({"pending_answer", "awaiting_approval"})
DONE_STATUSES = frozenset({"done", "completed", "complete", "success", "succeeded"})
FAILED_STATUSES = frozenset({"failed", "error", "cancelled", "canceled", "rejected", "aborted"})

# The step that publishes an approved template. Its presence in the job's own
# `completed_steps` is the only proof this app accepts that a heal reached
# production.
SAVE_STEP = "save_new_template"


class HealError(RuntimeError):
    """Anything Bright Data told us it could not do, or a job state we cannot
    honestly report as a heal."""


def _terse(text: str, limit: int = 200) -> str:
    return " ".join((text or "").split())[:limit]


class HealClient:
    """The three AI-flow calls. Same auth and base URL as the scrape client."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not settings.bd_api_key:
            raise HealError("BD_API_KEY is empty, so self-healing cannot be triggered")
        self._client = httpx.AsyncClient(
            base_url=settings.bd_base_url,
            headers={
                "Authorization": f"Bearer {settings.bd_api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
            transport=transport,
        )

    async def refactor_template(
        self, collector_id: str, prompt: str, custom_input: list[dict[str, Any]]
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"prompt": prompt}
        if custom_input:
            body["custom_input"] = custom_input
        response = await self._client.post(
            f"/dca/collectors/{collector_id}/refactor_template", json=body
        )
        return _payload(response, "refactor_template")

    async def progress(self, collector_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/dca/collectors/{collector_id}/refactor_template/progress"
        )
        return _payload(response, "refactor_template/progress")

    async def resume(
        self, collector_id: str, message: bool = True, auto_save: bool = True
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/dca/collectors/{collector_id}/resume_automation_job",
            json={"message": message, "auto_save": auto_save},
        )
        return _payload(response, "resume_automation_job")

    async def aclose(self) -> None:
        await self._client.aclose()


def _payload(response: httpx.Response, what: str) -> dict[str, Any]:
    """A JSON object, or an honest error naming the call that failed.

    `resume_automation_job` is documented as returning 200 with no body, so an
    empty or non-object body on a 2xx is reported as an empty dict rather than
    as a failure.
    """
    if response.status_code >= 400:
        raise HealError(f"{what} failed: HTTP {response.status_code} {_terse(response.text)}")
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {"payload": payload}


def status_of(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "").strip().lower()


def step_of(payload: dict[str, Any]) -> str:
    return str(payload.get("step") or "").strip()


def completed_steps(payload: dict[str, Any]) -> list[str]:
    steps = payload.get("completed_steps")
    if not isinstance(steps, list):
        return []
    return [str(step) for step in steps]


def preview_of(payload: dict[str, Any]) -> Any:
    """The sample output of the proposed fix, if the job published one.

    This is the row a reviewer checks against ground truth before approving. It
    is passed through unchanged: trimming it would hide the very thing an
    approval is supposed to be based on.
    """
    for key in ("preview_result", "preview", "result_preview"):
        if key in payload:
            return payload[key]
    return None


def validate_prompt(raw: str | None) -> str:
    prompt = " ".join((raw or "").split()) or DEFAULT_PROMPT
    if len(prompt) > PROMPT_MAX_LEN:
        raise HealError(
            f"heal prompt is {len(prompt)} characters, over the {PROMPT_MAX_LEN}-character limit"
        )
    return prompt


async def run_heal_cycle(
    store: EventStore,
    settings: Settings,
    client: HealClient,
    collector_id: str,
    prompt: str,
    custom_input: list[dict[str, Any]],
    universe: str = "chaos",
) -> None:
    """Trigger, poll to the approval gate, approve with auto_save, confirm the save.

    Emits the reserved heal events with Bright Data's own values. Raises
    `HealError` on anything it cannot honestly call a heal; the caller turns that
    into a `failed` event.
    """
    await store.append(
        EventType.HEAL_STARTED,
        {
            "prompt": prompt,
            "prompt_chars": len(prompt),
            "custom_input": custom_input,
            "poll_interval_s": settings.heal_poll_interval_s,
            "timeout_s": settings.heal_timeout_s,
        },
        universe=universe,
    )

    triggered = await client.refactor_template(collector_id, prompt, custom_input)
    trigger_status = status_of(triggered)
    if trigger_status in FAILED_STATUSES:
        raise HealError(f"self-healing was refused at trigger: {trigger_status}")

    gate = await _poll(store, settings, client, collector_id, universe, stop_on_approval=True)
    gate_status = status_of(gate)

    if gate_status in DONE_STATUSES:
        # The job ran to completion without ever asking. Nothing was approved
        # here, so `auto_save` never applied and there is no published template
        # to point at.
        await store.append(
            EventType.HEAL_PREVIEWED,
            {
                "status": gate_status,
                "step": step_of(gate),
                "completed_steps": completed_steps(gate),
                "preview_result": preview_of(gate),
            },
            universe=universe,
        )
        raise HealError(
            "self-healing finished without pausing at the approval gate, "
            "so nothing was approved and auto_save never ran"
        )

    await store.append(
        EventType.HEAL_PREVIEWED,
        {
            "status": gate_status,
            "step": step_of(gate),
            "completed_steps": completed_steps(gate),
            "preview_result": preview_of(gate),
        },
        universe=universe,
    )

    resumed = await client.resume(collector_id, message=True, auto_save=True)
    await store.append(
        EventType.HEAL_APPROVED,
        {
            "message": True,
            # The API body field that publishes the approved template. The CLI
            # spells the same thing --auto-approve.
            "auto_save": True,
            "response": resumed,
        },
        universe=universe,
    )

    final = await _poll(store, settings, client, collector_id, universe, stop_on_approval=False)
    final_status = status_of(final)
    steps = completed_steps(final)

    if final_status in FAILED_STATUSES:
        raise HealError(f"self-healing job ended {final_status} after approval")
    if SAVE_STEP not in steps:
        raise HealError(
            f"job ended {final_status or 'with no status'} but {SAVE_STEP!r} is absent from "
            "completed_steps, so the healed template was not published to production"
        )

    await store.append(
        EventType.HEAL_PROMOTED,
        {
            "status": final_status,
            "step": step_of(final),
            "completed_steps": steps,
            "template": final.get("template"),
        },
        universe=universe,
    )


async def _poll(
    store: EventStore,
    settings: Settings,
    client: HealClient,
    collector_id: str,
    universe: str,
    stop_on_approval: bool,
) -> dict[str, Any]:
    """Poll the job until it settles, reporting each new step as it happens.

    `completed_steps` is real per-stage telemetry that only AI jobs publish, so
    a step change is an observed state and is emitted as one. Nothing is emitted
    between two polls that reported the same step.
    """
    last_step = ""
    while True:
        payload = await client.progress(collector_id)
        status = status_of(payload)
        step = step_of(payload)

        if step and step != last_step:
            last_step = step
            await store.append(
                EventType.PROGRESS,
                {"step": step, "completed_steps": completed_steps(payload), "status": status},
                universe=universe,
            )

        if status in FAILED_STATUSES:
            raise HealError(f"self-healing job ended {status}")
        if status in DONE_STATUSES:
            return payload
        if stop_on_approval and status in APPROVAL_STATUSES:
            return payload

        await asyncio.sleep(settings.heal_poll_interval_s)
