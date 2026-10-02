"""The language model behind the agents and chat answers.

Any OpenAI-compatible endpoint works: Groq's free plan by default (the
open-weight openai/gpt-oss-120b, no credit card), or a self-hosted server such
as Ollama, llama.cpp or vLLM by changing LLM_BASE_URL and LLM_MODEL.

The app keeps itself on the free plan. It counts its own tokens per UTC day and
stops calling the model at llm_daily_token_budget, below the provider's free
limit; prompts it has seen before are answered from a cache. Whenever no answer
comes back -- no key, budget spent, an error -- callers fall back to their
template text, so the model is an improvement, never a dependency.
"""

import contextvars
import hashlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import LlmCacheModel, LlmUsageModel

logger = logging.getLogger(__name__)


@dataclass
class Meter:
    """Model use within one investigation or answer."""

    model: str | None = None
    calls: int = 0
    cached: int = 0
    skipped: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "cached": self.cached,
            "skipped": self.skipped,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.tokens,
        }


_meter: contextvars.ContextVar[Meter | None] = contextvars.ContextVar("llm_meter", default=None)


@contextmanager
def metered() -> Iterator[Meter]:
    """Count the model use of everything run inside the block."""
    meter = Meter(model=model_name())
    token = _meter.set(meter)
    try:
        yield meter
    finally:
        _meter.reset(token)


def _endpoint() -> tuple[str, str | None, str] | None:
    """(api_key, base_url, model), or None when no model is configured."""
    if settings.llm_api_key:
        return settings.llm_api_key, settings.llm_base_url, settings.llm_model
    if settings.openai_api_key:
        return settings.openai_api_key, None, settings.openai_chat_model
    return None


def model_name() -> str | None:
    endpoint = _endpoint()
    return endpoint[2] if endpoint else None


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def usage_today(db) -> LlmUsageModel:
    row = db.get(LlmUsageModel, _today())
    if row is None:
        row = LlmUsageModel(day=_today(), calls=0, cached=0, skipped=0, prompt_tokens=0, completion_tokens=0)
        db.add(row)
    return row


def chat_completion(system_prompt: str, user_prompt: str, *, json_object: bool = False) -> str | None:
    """The model's answer, or None when there is none to be had.

    json_object asks for a JSON object instead of prose, for answers code reads.
    """
    endpoint = _endpoint()
    if endpoint is None:
        return None
    api_key, base_url, model = endpoint
    meter = _meter.get()
    request = f"{model}\n{system_prompt}\n{user_prompt}" + ("\njson" if json_object else "")
    key = hashlib.sha256(request.encode("utf-8")).hexdigest()

    with SessionLocal() as db:
        usage = usage_today(db)
        cached = db.get(LlmCacheModel, key)
        if cached is not None:
            usage.cached += 1
            if meter:
                meter.cached += 1
            db.commit()
            return cached.response

        if usage.prompt_tokens + usage.completion_tokens >= settings.llm_daily_token_budget:
            usage.skipped += 1
            if meter:
                meter.skipped += 1
            db.commit()
            logger.info("Daily model token budget spent; using template text")
            return None

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=1)
            options = {
                "model": model,
                "temperature": 0.2,
                "max_completion_tokens": settings.llm_max_output_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            if settings.llm_reasoning_effort and base_url:
                options["reasoning_effort"] = settings.llm_reasoning_effort
            if json_object:
                options["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**options)
        except Exception as exc:  # noqa: BLE001 - any failure means "use the template"
            logger.warning("Language model call failed; using template text: %s", exc)
            usage.skipped += 1
            if meter:
                meter.skipped += 1
            db.commit()
            return None

        content = (response.choices[0].message.content or "").strip()
        prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
        usage.calls += 1
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        if meter:
            meter.calls += 1
            meter.prompt_tokens += prompt_tokens
            meter.completion_tokens += completion_tokens
        if content:
            db.add(
                LlmCacheModel(
                    key=key,
                    model=model,
                    response=content,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    created_at=datetime.now(timezone.utc),
                )
            )
        db.commit()
        return content or None
