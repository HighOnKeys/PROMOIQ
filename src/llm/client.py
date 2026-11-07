"""Model abstraction: one interface, several interchangeable providers.

Providers
---------
heuristic       no model; deterministic code paths are used instead (default, free, offline)
gemini          Google Gemini via google-generativeai  (GEMINI_API_KEY)
anthropic       Anthropic Claude via anthropic SDK      (ANTHROPIC_API_KEY)
openai_compat   any OpenAI-compatible /chat/completions (OPENAI_API_KEY, OPENAI_BASE_URL)
                e.g. OpenAI, Groq, OpenRouter, Together, a local vLLM/Ollama server

Every call is cached on disk (keyed by provider+model+prompt), every raw response is
appended unedited to outputs/raw/llm_responses.jsonl, and usage is counted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "anthropic": "claude-haiku-5-5",
    "openai_compat": "gpt-4o-mini",
}


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    prompt_chars: int = 0
    response_chars: int = 0
    seconds: float = 0.0
    by_task: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        approx_in = self.prompt_chars // 4
        approx_out = self.response_chars // 4
        return {
            "calls": self.calls, "cache_hits": self.cache_hits, "failures": self.failures,
            "approx_input_tokens": approx_in, "approx_output_tokens": approx_out,
            "seconds": round(self.seconds, 2), "by_task": self.by_task,
        }


class LLMClient:
    provider = "base"

    def __init__(self, model: str, temperature: float = 0.2):
        self.model = model
        self.temperature = temperature

    def _generate(self, system: str, prompt: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self, model: str, temperature: float = 0.2):
        super().__init__(model, temperature)
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set")
        import google.generativeai as genai

        genai.configure(api_key=key)
        self._genai = genai

    def _generate(self, system: str, prompt: str) -> str:
        model = self._genai.GenerativeModel(self.model, system_instruction=system)
        resp = model.generate_content(
            prompt,
            generation_config={"temperature": self.temperature, "response_mime_type": "application/json"},
        )
        return resp.text


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, model: str, temperature: float = 0.2):
        super().__init__(model, temperature)
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        import anthropic

        base = os.environ.get("PROMOIQ_ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self._client = anthropic.Anthropic(api_key=key, base_url=base)

    def _generate(self, system: str, prompt: str) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=8000, temperature=self.temperature,
            system=system, messages=[{"role": "user", "content": prompt}],
        )
        return "".join(getattr(b, "text", "") for b in msg.content)


class OpenAICompatClient(LLMClient):
    provider = "openai_compat"

    def __init__(self, model: str, temperature: float = 0.2):
        super().__init__(model, temperature)
        self._key = os.environ.get("OPENAI_API_KEY", "")
        self._base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        if not self._key and "localhost" not in self._base and "127.0.0.1" not in self._base:
            raise LLMError("OPENAI_API_KEY is not set")

    def _generate(self, system: str, prompt: str) -> str:
        import requests

        r = requests.post(
            f"{self._base}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"} if self._key else {},
            json={
                "model": self.model, "temperature": self.temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


_PROVIDERS = {"gemini": GeminiClient, "anthropic": AnthropicClient, "openai_compat": OpenAICompatClient}


class ModelGateway:
    """Cached, logged, usage-tracked access to one provider/model."""

    def __init__(self, client: LLMClient, cache_dir: Path, raw_log: Path, max_retries: int = 2):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_log = Path(raw_log)
        self.raw_log.parent.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.usage = Usage()

    @property
    def name(self) -> str:
        return f"{self.client.provider}:{self.client.model}"

    def _key(self, system: str, prompt: str) -> str:
        h = hashlib.sha256(f"{self.name}\n{system}\n{prompt}".encode()).hexdigest()
        return h[:32]

    def generate(self, task: str, system: str, prompt: str) -> str:
        key = self._key(system, prompt)
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            self.usage.cache_hits += 1
            return json.loads(path.read_text())["response"]
        t0 = time.time()
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                text = self.client._generate(system, prompt)
                break
            except Exception as exc:  # network/API errors are retried, then surfaced
                last_exc = exc
                log.warning("%s call failed (attempt %d): %s", self.name, attempt + 1, exc)
                time.sleep(2 * (attempt + 1))
        else:
            self.usage.failures += 1
            raise LLMError(f"{self.name} failed after retries: {last_exc}")
        dt = time.time() - t0
        self.usage.calls += 1
        self.usage.seconds += dt
        self.usage.prompt_chars += len(system) + len(prompt)
        self.usage.response_chars += len(text)
        self.usage.by_task[task] = self.usage.by_task.get(task, 0) + 1
        path.write_text(json.dumps({"model": self.name, "task": task, "response": text}))
        with self.raw_log.open("a") as fh:  # unedited raw responses
            fh.write(json.dumps({"ts": time.time(), "model": self.name, "task": task,
                                 "prompt_sha": key, "response": text}) + "\n")
        return text

    def generate_json(self, task: str, system: str, prompt: str, schema: Type[T]) -> T:
        """Generate, extract JSON, validate with pydantic; one corrective retry."""
        text = self.generate(task, system, prompt)
        try:
            return schema.model_validate(extract_json(text))
        except (ValueError, ValidationError) as exc:
            log.warning("invalid JSON from %s for %s: %s", self.name, task, str(exc)[:200])
            fix = (prompt + "\n\nYour previous answer was invalid: " + str(exc)[:500]
                   + "\nReturn ONLY valid JSON matching the requested schema.")
            text = self.generate(task + ":retry", system, fix)
            return schema.model_validate(extract_json(text))


def extract_json(text: str):
    """Pull the first JSON object/array out of a model response (handles code fences)."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        a, b = t.find(open_c), t.rfind(close_c)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no parseable JSON in model response")


def make_gateway(provider: str, model: str, cache_dir: Path, raw_log: Path,
                 temperature: float = 0.2, max_retries: int = 2) -> Optional[ModelGateway]:
    """Return a gateway, or None for the offline heuristic provider."""
    provider = (provider or "heuristic").lower()
    if provider in ("heuristic", "none", "offline", ""):
        return None
    if provider not in _PROVIDERS:
        raise LLMError(f"unknown provider {provider!r}; choose heuristic|{'|'.join(_PROVIDERS)}")
    client = _PROVIDERS[provider](model or DEFAULT_MODELS[provider], temperature)
    return ModelGateway(client, cache_dir, raw_log, max_retries)
