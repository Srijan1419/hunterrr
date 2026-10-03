"""Find which NVIDIA chat models answer right now, once at the start of a run.

Owned by task h2-05b. NVIDIA's free catalogue changes by the hour: a model id that
answered at one o'clock returns 404 or 410 at four, and the `/v1/models` catalogue
lists deployments a free key cannot call at all. So a pinned model list rots, and the
router's 404/410 quarantine only learns that one model at a time, one failed call at a
time. Discovery learns it up front instead: list the catalogue, throw away the ids that
cannot be chat models, and send each remaining candidate a tiny "return JSON" probe.

`discover_models` never raises and never touches the network itself: the HTTP layer is
the injected `http` callable, so tests drive this with a stub and production passes a
thin adapter (built in `router.py`, where the rate-limit bucket lives). Anything that
goes wrong — the catalogue is unreachable, a probe times out, the budget is spent —
means "fewer models found", never an exception.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence

from .prompts import extract_json_object, response_text

logger = logging.getLogger(__name__)

#: `http(method, url, *, headers, body, timeout) -> (status, payload)`.
#: `payload` is the decoded JSON body (a chat-completions envelope for probes, the
#: model list for the catalogue). It raises on a network failure, which per-probe
#: handling turns into "that model is out".
Http = Callable[..., Awaitable[tuple[int, Any]]]

#: Substrings (matched case-insensitively) that mark a catalogue id as not-a-chat-model.
#: `retriev` covers retrieval/retrieve/retriever; the rest are literal per the task.
EXCLUDED_MODEL_MARKERS: tuple[str, ...] = (
    "embed",
    "rerank",
    "guard",
    "safety",
    "clip",
    "vision",
    "parse",
    "retriev",
    "reward",
    "tts",
    "asr",
    "diffusion",
    "flux",
    "cosmos",
)

#: Never more probes than this in one discovery run (criterion 4: at most 10 tokens).
MAX_PROBES = 10

#: Probes in flight at once. Six keeps a 10-probe run to two waves.
MAX_PARALLEL = 6

#: One probe's own timeout. The overall `budget_seconds` is the tighter bound in
#: practice (its default is below this), but a lone slow model must not hold a wave.
PROBE_TIMEOUT_SECONDS = 25.0

#: The probe asks for three tokens of JSON; 200 is headroom for a reasoning spill.
PROBE_MAX_TOKENS = 200

#: The whole probe. Small on purpose: anything that can answer JSON answers this.
PROBE_PROMPT = 'Return only JSON {"ok":true}'


async def discover_models(
    provider: Any,
    http: Http,
    *,
    preferred: Sequence[str],
    budget_seconds: float = 20,
) -> list[str]:
    """The catalogue ids that answered a JSON probe with 200, best first. Never raises.

    `provider` is a `Provider` (its base URL, key and `request_options` are read from
    it); `preferred` orders the result — position in `preferred` first, probe latency
    second. Only the first 10 candidates after preferred-ordering are probed, 6 at a
    time, and the whole call keeps within `budget_seconds`, returning whatever was
    found when the budget runs out (possibly nothing).
    """
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, float(budget_seconds))
        catalogue = await _list_chat_ids(provider, http, deadline)
        if not catalogue:
            return []
        # Probe the most-preferred candidates: ordering by preference *before* the
        # 10-probe cap is what makes the cap spend itself on the best models.
        rank = {model: index for index, model in enumerate(preferred)}
        ordered = sorted(catalogue, key=lambda m: (rank.get(m, len(rank)), catalogue.index(m)))
        candidates = ordered[:MAX_PROBES]
        found = await _probe_all(provider, http, candidates, rank, deadline)
        found.sort(key=lambda item: (item[1], item[2]))
        logger.info(
            "discovery: %d of %d probed nvidia models answer (%d catalogue ids)",
            len(found),
            len(candidates),
            len(catalogue),
        )
        return [model for model, _, _ in found]
    except Exception as error:  # noqa: BLE001 - discovery must never take a run down
        logger.warning("discovery failed; falling back to configured models: %s", error)
        return []


async def _list_chat_ids(provider: Any, http: Http, deadline: float) -> list[str]:
    """The catalogue's chat-model ids, filtered and deduped, or `[]` on any failure."""
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        return []
    base = (getattr(getattr(provider, "spec", provider), "base_url", "") or "").rstrip("/")
    headers = _headers(provider)
    status, payload = await asyncio.wait_for(
        http("GET", f"{base}/models", headers=headers, body=None,
             timeout=min(PROBE_TIMEOUT_SECONDS, remaining)),
        timeout=remaining,
    )
    if status != 200:
        return []
    ids = _ids_from(payload)
    seen: set[str] = set()
    chat: list[str] = []
    for model in ids:
        if not model or model in seen or _excluded(model):
            continue
        seen.add(model)
        chat.append(model)
    return chat


async def _probe_all(
    provider: Any,
    http: Http,
    candidates: Sequence[str],
    rank: Mapping[str, int],
    deadline: float,
) -> list[tuple[str, int, float]]:
    """Probe each candidate; return `(model, preferred-rank, latency)` for the winners."""
    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(MAX_PARALLEL)

    async def one(model: str) -> tuple[str, int, float] | None:
        async with semaphore:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            base = (getattr(getattr(provider, "spec", provider), "base_url", "") or "").rstrip("/")
            start = loop.time()
            try:
                status, payload = await asyncio.wait_for(
                    http(
                        "POST",
                        f"{base}/chat/completions",
                        headers=_headers(provider),
                        body=_probe_body(provider, model),
                        timeout=min(PROBE_TIMEOUT_SECONDS, remaining),
                    ),
                    timeout=remaining,
                )
            except Exception:
                return None
            if status != 200:
                return None
            try:
                parsed = extract_json_object(response_text(payload))
            except Exception:
                return None
            if not isinstance(parsed, dict):
                return None
            return (model, rank.get(model, len(rank)), loop.time() - start)

    results = await asyncio.gather(*(one(model) for model in candidates))
    return [item for item in results if item is not None]


def _probe_body(provider: Any, model: str) -> dict:
    """The tiny JSON probe. `request_options` ride underneath, never on top.

    The options are the NIM `enable_thinking: false` switch: without it a Nemotron
    model spends its 200 tokens on chain-of-thought and the probe reads that as
    "this model cannot answer JSON", which is the wrong thing to learn.
    """
    options = dict(getattr(getattr(provider, "spec", provider), "request_options", {}) or {})
    return {
        **options,
        "model": model,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "temperature": 0,
        "max_tokens": PROBE_MAX_TOKENS,
    }


def _headers(provider: Any) -> dict:
    headers = {"Accept": "application/json"}
    key = getattr(provider, "api_key", "") or ""
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _ids_from(payload: Any) -> list[str]:
    """Catalogue ids out of an OpenAI-style list body (`{"data": [{"id": ...}]}`).

    Also accepts a bare list of `{"id": ...}` dicts or plain strings, because
    gateways vary and the strict shape is not the thing under test.
    """
    items = payload.get("data") if isinstance(payload, Mapping) else payload
    if isinstance(payload, dict) and not isinstance(items, list):
        single = payload.get("id")
        items = [single] if single else []
    if not isinstance(items, list):
        return []
    ids: list[str] = []
    for item in items:
        if isinstance(item, str):
            ids.append(item.strip())
        elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
            ids.append(str(item["id"]).strip())
    return [model for model in ids if model]


def _excluded(model: str) -> bool:
    lowered = model.casefold()
    return any(marker in lowered for marker in EXCLUDED_MODEL_MARKERS)
