"""OpenAI (Responses API). The default provider, and the one the held-out run uses."""

from __future__ import annotations

import json
import os

from .base import LLMUnavailable, b64, provider_failure


class OpenAIProvider:
    name = "openai"
    key_env = "OPENAI_API_KEY"
    default_model = "gpt-6.1-terra"

    # USD per 1M tokens as (input, cached input, output). Standard tier, read from
    # https://developers.openai.com/api/docs/pricing on 2026-11-14.
    prices = {
        "gpt-6-astra": (10.00, 1.00, 50.00),
        "gpt-6.1-sol": (2.00, 0.10, 10.00),
        "gpt-6.1-terra": (1.00, 0.05, 5.00),
        "gpt-6-luna": (0.10, 0.01, 0.50),
    }

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get(self.key_env)
        self._client = None

    def build_request(self, *, model, task, instructions, parts, schema, effort) -> dict:
        return {
            "model": model,
            "instructions": instructions,
            "input": [{"role": "user", "content": [self._part(p) for p in parts]}],
            "text": {"format": {"type": "json_schema", "name": task, "strict": True, "schema": schema}},
            "reasoning": {"effort": effort},
            "store": False,  # customer emails are not kept on the provider's side
        }

    @staticmethod
    def _part(part: dict) -> dict:
        if part["kind"] == "text":
            return {"type": "input_text", "text": part["text"]}
        if part["kind"] == "image":
            url = f"data:{part['mime']};base64,{b64(part['data'])}"
            return {"type": "input_image", "image_url": url, "detail": "high"}
        # A fixed filename keeps the cache key a function of the file's bytes only.
        url = f"data:application/pdf;base64,{b64(part['data'])}"
        return {"type": "input_file", "filename": "document.pdf", "file_data": url}

    def call(self, request: dict) -> dict:
        if not self._api_key:
            raise LLMUnavailable(f"{self.key_env} is not set and this request is not in the cache")
        try:
            response = self._sdk().responses.create(**request)
        except Exception as exc:
            raise provider_failure(exc) from exc
        if response.status != "completed":
            raise LLMUnavailable(f"provider response was {response.status}, not completed")
        try:
            output = json.loads(response.output_text)
        except ValueError as exc:  # a refusal or cut-off answer is not valid JSON
            raise LLMUnavailable("model answer was not valid JSON") from exc
        details = getattr(response.usage, "input_tokens_details", None)
        return {
            "model": request["model"],
            "output": output,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "cached_input_tokens": getattr(details, "cached_tokens", 0) or 0,
                "output_tokens": response.usage.output_tokens,
            },
        }

    def _sdk(self):
        if self._client is None:
            from openai import OpenAI  # imported late: a cached replay does not need the SDK

            self._client = OpenAI(api_key=self._api_key, timeout=120, max_retries=3)
        return self._client
