"""The one entry point for model calls, whichever provider is in use.

complete() answers from the cache when it can, calls the provider when it must, and
raises LLMUnavailable when it can do neither. Callers catch LLMUnavailable and degrade;
nothing here is allowed to crash a run.

The provider is OpenAI unless RFQ_LLM_PROVIDER=anthropic (or provider="anthropic") says otherwise.
"""

from __future__ import annotations

import os

from .anthropic_provider import AnthropicProvider
from .base import text_part
from .cache import ResponseCache, request_key
from .openai_provider import OpenAIProvider

DEFAULT_PROVIDER = "openai"
PROVIDERS = {"openai": OpenAIProvider, "anthropic": AnthropicProvider}
PRICES = {**OpenAIProvider.prices, **AnthropicProvider.prices}


def cost_usd(model: str, usage: dict) -> float | None:
    """Dollar cost of one call, or None when the model's price is not in PRICES."""
    if model not in PRICES:
        return None
    price_in, price_cached, price_out = PRICES[model]
    cached = usage["cached_input_tokens"]
    fresh = usage["input_tokens"] - cached
    return (fresh * price_in + cached * price_cached + usage["output_tokens"] * price_out) / 1_000_000


class LLMClient:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        cache: ResponseCache | None = None,
        api_key: str | None = None,
        effort: str = "low",
    ):
        name = provider or os.environ.get("RFQ_LLM_PROVIDER") or DEFAULT_PROVIDER
        if name not in PROVIDERS:
            raise ValueError(f"unknown LLM provider {name!r}; choose one of {sorted(PROVIDERS)}")
        self.provider = PROVIDERS[name](api_key=api_key)
        self.model = model or os.environ.get("RFQ_LLM_MODEL") or self.provider.default_model
        self.effort = effort
        self.cache = cache or ResponseCache()
        # One record per complete() call, cached or live. The cost report is built from this.
        self.calls: list[dict] = []

    def complete(
        self, *, task: str, label: str, instructions: str, content: list[dict], schema: dict, cache_identity: str | None = None
    ) -> dict:
        """Return the model's JSON answer for this request. `label` is the email id, for the ledger.

        The answer is saved under a hash of the whole request. With `cache_identity`, that text
        stands in for the content when the hash is made: "page 3 of the file with this hash" is
        then the same request on every machine, however the page's bytes were produced.
        """

        def build(parts: list[dict]) -> dict:
            return self.provider.build_request(
                model=self.model, task=task, instructions=instructions, parts=parts, schema=schema, effort=self.effort
            )

        request = build(content)
        keyed = request if cache_identity is None else build([text_part(cache_identity)])
        key = request_key({"provider": self.provider.name, "request": keyed})
        entry = self.cache.get(key)
        from_cache = entry is not None
        if not from_cache:
            entry = {"task": task, "label": label, "provider": self.provider.name, **self.provider.call(request)}
            self.cache.put(key, entry)
        self.calls.append(
            {
                "label": label,
                "task": task,
                "provider": entry["provider"],
                "model": entry["model"],
                "from_cache": from_cache,
                "usage": entry["usage"],
                "cost_usd": cost_usd(entry["model"], entry["usage"]),
            }
        )
        return entry["output"]
