"""One live call to check that a provider works before a full run.

    python -m src.llm.smoke            # uses PROMOIQ_PROVIDER / PROMOIQ_MODEL from env or .env

Sends no screenplay content: the probe is a synthetic one-line scene.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel

from config import get_settings
from src.llm.client import DEFAULT_MODELS, make_gateway


class _Probe(BaseModel):
    hook_score: int
    reason: str


def main() -> int:
    st = get_settings()
    if st.model.provider in ("", "heuristic"):
        print("PROMOIQ_PROVIDER is 'heuristic' (offline). Set PROMOIQ_PROVIDER=gemini and GEMINI_API_KEY in .env.")
        return 1
    tmp = Path(tempfile.mkdtemp())
    try:
        gw = make_gateway(st.model.provider, st.model.model, tmp / "cache", tmp / "raw.jsonl")
    except Exception as exc:
        print(f"FAILED to create client: {exc}")
        return 1
    print(f"provider/model: {gw.name}  (default for provider: {DEFAULT_MODELS.get(st.model.provider)})")
    lister = getattr(gw.client, "list_models", None)
    if lister:
        try:
            names = lister()
            flash = [n for n in names if "flash" in n or "pro" in n]
            print(f"models visible to this key ({len(names)}): " + ", ".join(sorted(flash)[:25]))
        except Exception as exc:
            print(f"(could not list models: {str(exc)[:200]})")
    t0 = time.time()
    try:
        r = gw.generate_json("smoke", "Return JSON only.",
                             'Scene: a bride is abandoned at the altar and screams "You knew!". '
                             'Return {"hook_score": 0-5 integer, "reason": "<=12 words"}.', _Probe)
    except Exception as exc:
        print(f"FAILED live call: {str(exc)[:500]}")
        return 1
    print(f"OK in {time.time() - t0:.1f}s -> {r.model_dump()}  tokens(in,out)={gw.real_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
