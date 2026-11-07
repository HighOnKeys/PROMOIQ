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
from typing import Optional, Type, TypeVar, get_origin

from pydantic import BaseModel, ValidationError, model_validator

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",   # gemini-2.5-flash is closed to new keys (404, Nov 2026)
    "anthropic": "claude-haiku-5-5",
    "openai_compat": "gpt-4o-mini",
}


class LLMError(RuntimeError):
    pass


class LenientModel(BaseModel):
    """Base for every schema an LLM fills in. Coerces harmless shape differences that real models
    produce (observed with gemini-3.6-flash: a list of sentences where a string was asked for;
    numbers and booleans as strings). Wrong *content* still fails validation."""

    @model_validator(mode="before")
    @classmethod
    def _coerce_shapes(cls, data):
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name, f in cls.model_fields.items():
            if name not in out:
                continue
            v, ann = out[name], f.annotation
            origin = get_origin(ann)
            if ann is str:
                if isinstance(v, list):
                    out[name] = "; ".join(str(x) for x in v)
                elif v is None:
                    out[name] = ""
                elif not isinstance(v, str):
                    out[name] = str(v)
            elif origin is list and isinstance(v, str):
                out[name] = [v] if v.strip() else []
            elif ann is bool and isinstance(v, str):
                out[name] = v.strip().lower() in ("true", "yes", "1", "y")
            elif ann in (float, int) and isinstance(v, str):
                m = re.search(r"-?\d+(?:\.\d+)?", v)
                if m:
                    out[name] = float(m.group(0)) if ann is float else int(float(m.group(0)))
        return out


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    prompt_chars: int = 0
    response_chars: int = 0
    seconds: float = 0.0
    by_task: dict = field(default_factory=dict)

    def as_dict(self, real_tokens: Optional[list] = None) -> dict:
        approx_in = self.prompt_chars // 4
        approx_out = self.response_chars // 4
        d = {
            "calls": self.calls, "cache_hits": self.cache_hits, "failures": self.failures,
            "approx_input_tokens": approx_in, "approx_output_tokens": approx_out,
            "seconds": round(self.seconds, 2), "by_task": self.by_task,
        }
        if real_tokens and any(real_tokens):
            d["reported_input_tokens"], d["reported_output_tokens"] = real_tokens
        return d


class LLMClient:
    provider = "base"

    def __init__(self, model: str, temperature: float = 0.2):
        self.model = model
        self.temperature = temperature

    def _generate(self, system: str, prompt: str) -> str:  # pragma: no cover - abstract
        raise NotImplementedError


class GeminiClient(LLMClient):
    """Gemini via the current google-genai SDK (falls back to legacy google-generativeai).

    PROMOIQ_GEMINI_THINKING_BUDGET (default 0) turns off "thinking" on Flash models: faster and
    cheaper for structured scoring. Set it empty to use the model default."""
    provider = "gemini"

    def __init__(self, model: str, temperature: float = 0.2):
        super().__init__(model, temperature)
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set")
        self.thinking_budget = os.environ.get("PROMOIQ_GEMINI_THINKING_BUDGET", "0")
        self._thinking_ok = True
        try:
            from google import genai
            from google.genai import types

            self._client = genai.Client(api_key=key)
            self._types = types
            self.sdk = "google-genai"
        except ImportError:  # pragma: no cover - legacy path
            import google.generativeai as legacy

            legacy.configure(api_key=key)
            self._legacy = legacy
            self.sdk = "google-generativeai"

    def _generate(self, system: str, prompt: str) -> str:
        if self.sdk != "google-genai":  # pragma: no cover
            model = self._legacy.GenerativeModel(self.model, system_instruction=system)
            resp = model.generate_content(prompt, generation_config={
                "temperature": self.temperature, "response_mime_type": "application/json"})
            return resp.text
        t = self._types
        kw = dict(system_instruction=system, temperature=self.temperature, response_mime_type="application/json")
        if self.thinking_budget != "" and self._thinking_ok:
            kw["thinking_config"] = t.ThinkingConfig(thinking_budget=int(self.thinking_budget))
        try:
            resp = self._client.models.generate_content(model=self.model, contents=prompt,
                                                        config=t.GenerateContentConfig(**kw))
        except Exception as exc:
            if "thinking_config" in kw and "thinking" in str(exc).lower():
                self._thinking_ok = False          # model does not accept a thinking budget
                kw.pop("thinking_config")
                resp = self._client.models.generate_content(model=self.model, contents=prompt,
                                                            config=t.GenerateContentConfig(**kw))
            else:
                raise
        um = getattr(resp, "usage_metadata", None)
        self.last_usage = (getattr(um, "prompt_token_count", 0) or 0, getattr(um, "candidates_token_count", 0) or 0)
        text = resp.text
        if not text:
            reason = getattr(resp.candidates[0], "finish_reason", "?") if getattr(resp, "candidates", None) else "?"
            raise LLMError(f"empty response from {self.model} (finish_reason={reason})")
        return text

    def list_models(self) -> list[str]:
        return [m.name for m in self._client.models.list()]


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


_LAST_CALL: dict[str, float] = {}      # provider -> wall time of last live call (shared across gateways)
_DEFAULT_INTERVAL = {"gemini": 13.0}   # free tier: ~5 requests/minute observed; override with PROMOIQ_MIN_CALL_INTERVAL_S
_RETRY_IN = re.compile(r"(?:retry in|retryDelay['\"]?:\s*['\"]?)\s*([\d.]+)\s*s", re.I)


def _is_rate_limit(exc: Exception) -> bool:
    """Rate limits AND transient overload (503): both mean 'wait and retry', not 'give up'."""
    s = str(exc)
    return ("429" in s or "RESOURCE_EXHAUSTED" in s or "rate limit" in s.lower() or "quota" in s.lower()
            or "503" in s[:40] or "UNAVAILABLE" in s or "overloaded" in s.lower() or "high demand" in s.lower())


def _is_permanent(exc: Exception) -> bool:
    """Bad request / auth / unknown model: retrying cannot help."""
    s = str(exc)[:40]
    return any(code in s for code in ("400", "401", "403", "404", "INVALID_ARGUMENT", "NOT_FOUND", "PERMISSION_DENIED"))


_QUOTA_ID = re.compile(r"quotaId['\"]?:\s*['\"]([^'\"]+)")


def _quota_ids(exc: Exception) -> list[str]:
    return _QUOTA_ID.findall(str(exc))


def _is_daily_quota(exc: Exception) -> bool:
    """Only the provider's own quota id decides (an earlier substring check on 'daily' misread a
    per-minute 429 as daily exhaustion and stopped 38 calls that would have succeeded)."""
    ids = _quota_ids(exc)
    if ids:
        return any("PerDay" in q for q in ids) and not any("PerMinute" in q for q in ids)
    return "PerDay" in str(exc)


class ModelGateway:
    """Cached, logged, usage-tracked, rate-limited access to one provider/model."""

    def __init__(self, client: LLMClient, cache_dir: Path, raw_log: Path, max_retries: int = 2):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.raw_log = Path(raw_log)
        self.raw_log.parent.mkdir(parents=True, exist_ok=True)
        self.max_retries = max_retries
        self.usage = Usage()
        self.min_interval = float(os.environ.get("PROMOIQ_MIN_CALL_INTERVAL_S",
                                                 _DEFAULT_INTERVAL.get(client.provider, 0.0)))
        self.exhausted = False          # set when the daily quota is gone: later calls fail fast
        self.real_tokens = [0, 0]       # provider-reported (input, output) tokens when available

    def _pace(self) -> None:
        last = _LAST_CALL.get(self.client.provider, 0.0)
        wait = last + self.min_interval - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[self.client.provider] = time.time()

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
        if self.exhausted:
            self.usage.failures += 1
            raise LLMError(f"{self.name}: daily quota exhausted earlier in this run; not calling")
        t0 = time.time()
        last_exc: Optional[Exception] = None
        text = None
        errors = rate_hits = 0
        while text is None:
            self._pace()
            try:
                text = self.client._generate(system, prompt)
            except Exception as exc:  # network/API errors are retried, then surfaced
                last_exc = exc
                if _is_rate_limit(exc):
                    qids = _quota_ids(exc)
                    if _is_daily_quota(exc):
                        self.exhausted = True
                        self.usage.failures += 1
                        raise LLMError(f"{self.name}: daily quota exhausted (quotaId={qids}): {str(exc)[:200]}")
                    rate_hits += 1
                    if rate_hits > 6:
                        break
                    m = _RETRY_IN.search(str(exc))
                    per_minute = any("PerMinute" in q for q in qids)
                    delay = float(m.group(1)) + 1.0 if m else (61.0 if per_minute else min(90.0, 15.0 * rate_hits))
                    log.warning("%s rate-limited/unavailable (%s); waiting %.0fs (hit %d)", self.name,
                                qids or str(exc)[:30], delay, rate_hits)
                    time.sleep(delay)
                    continue
                errors += 1
                log.warning("%s call failed (attempt %d): %s", self.name, errors, str(exc)[:300])
                if errors > self.max_retries or _is_permanent(exc):
                    break
                time.sleep(2 * errors)
        if text is None:
            self.usage.failures += 1
            raise LLMError(f"{self.name} failed after retries: {str(last_exc)[:300]}")
        lu = getattr(self.client, "last_usage", None)
        if lu:
            self.real_tokens[0] += lu[0]
            self.real_tokens[1] += lu[1]
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
