"""Join blind ratings with the hidden key and summarise per system.

    python -m evaluation.review_summary [ratings.csv] [pairwise.csv]

Defaults to the AI-assistant review. A human reviewer fills
evaluation/human_eval/human_ratings_TEMPLATE.csv (same columns) and passes it here.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from config import ROOT

PRIV = ROOT / "outputs" / "evaluation"
HE = ROOT / "evaluation" / "human_eval"
DIMS = ("hook", "cliffhanger", "standalone", "emotional_appeal", "coherence")


def main():
    ratings = Path(sys.argv[1]) if len(sys.argv) > 1 else HE / "ai_review_ratings.csv"
    pairwise = Path(sys.argv[2]) if len(sys.argv) > 2 else HE / "ai_pairwise.csv"
    # The AI review was done on round-1 sheets; human ratings use the current sheets.
    round1 = ratings.name.startswith("ai_")
    keydir = ROOT / "outputs" / "evaluation_round1" if round1 else PRIV
    resdir = ROOT / "evaluation" / "results" / ("round1" if round1 else "")
    key = json.loads((keydir / "blind_key.json").read_text())
    pkey = json.loads((keydir / "pairwise_key.json").read_text())
    bench = json.loads((resdir / "benchmark.json").read_text())
    per_clip = {(m["system"] if m["system"] != "PROMOIQ" else "PROMOIQ", m["clip_id"]): m
                for sysname, ms in bench["per_clip"].items() for m in ms}

    by_sys = defaultdict(list)
    rows = list(csv.DictReader(ratings.open()))
    for r in rows:
        k = key[r["blind_id"]]
        by_sys[k["system"]].append(r)
    out = {"ratings_file": ratings.name, "reviewer": rows[0]["reviewer"] if rows else "", "systems": {}}
    for s, rs in by_sys.items():
        agg = {d: round(sum(float(r[d]) for r in rs) / len(rs), 2) for d in DIMS}
        agg["overall"] = round(sum(agg.values()) / len(DIMS), 2)
        agg["n"] = len(rs)
        agg["clips_with_timestamp_issue"] = sum(1 for r in rs if r["timestamp_issue"] not in ("none", ""))
        agg["share_rated_4plus_overall"] = round(sum(1 for r in rs if sum(float(r[d]) for d in DIMS) / 5 >= 4) / len(rs), 2)
        out["systems"][s] = agg

    wins = defaultdict(int)
    agree = 0
    pw = list(csv.DictReader(pairwise.open()))
    details = []
    for r in pw:
        x, y = pkey[r["pair"]]["X"], pkey[r["pair"]]["Y"]
        chosen = x if r["preferred"] == "X" else y
        wins[chosen.split(":")[0]] += 1
        # model decision = higher mean of PROMOIQ's hook/cliffhanger/clarity scorers on each clip
        def model_score(tag):
            sysname, cid = tag.split(":")
            m = per_clip.get((sysname, cid)) or next(mm for (s2, c2), mm in per_clip.items() if c2 == cid)
            return (m["hook"] + m["cliff"] + m["clarity"]) / 3
        model_choice = x if model_score(x) >= model_score(y) else y
        agree += model_choice == chosen
        details.append({"pair": r["pair"], "X": x, "Y": y, "reviewer_choice": chosen, "model_choice": model_choice,
                        "reason": r["reason"]})
    out["pairwise"] = {"wins": dict(wins), "n": len(pw), "reviewer_model_agreement": f"{agree}/{len(pw)}",
                       "details": details}
    dest = resdir / f"review_summary_{ratings.stem}.json"
    dest.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
