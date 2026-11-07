"""Compare the offline run with an LLM run on the same pack.

    python -m evaluation.compare_runs [--llm outputs/gemini]

Writes evaluation/results/compare_offline_vs_llm.json (numbers only, no script text).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from scipy.stats import spearmanr

from config import ROOT

FEATURES = ("surprise", "emotional_intensity", "conflict_intensity", "stakes", "reveal_value",
            "visual_potential", "curiosity", "narrative_importance")


def load(p: Path):
    return json.loads(p.read_text())


def interval_overlap(a, b) -> float:
    ov = sum(max(0.0, min(x["end_s"], y["end_s"]) - max(x["start_s"], y["start_s"]))
             for x in a["segments"] for y in b["segments"] if x["episode"] == y["episode"])
    return ov / max(1e-6, min(a["total_duration_s"], b["total_duration_s"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", type=Path, default=ROOT / "outputs")
    ap.add_argument("--llm", type=Path, default=ROOT / "outputs" / "gemini")
    a = ap.parse_args()
    off_ann = {x["beat_id"]: x for x in load(a.offline / "raw/dramatic_events.json")}
    llm_ann = {x["beat_id"]: x for x in load(a.llm / "raw/dramatic_events.json")}
    src = Counter(x["annotator"] for x in llm_ann.values())
    llm_only = [b for b, x in llm_ann.items() if not x["annotator"].startswith("heuristic") and b in off_ann]
    out = {"beats": len(llm_ann), "llm_annotated_beats": len(llm_only), "annotation_sources": dict(src)}

    corr = {}
    for f in FEATURES:
        xs = [off_ann[b]["scores"].get(f, 0) for b in llm_only]
        ys = [llm_ann[b]["scores"].get(f, 0) for b in llm_only]
        if len(set(xs)) > 1 and len(set(ys)) > 1:
            r, p = spearmanr(xs, ys)
            corr[f] = {"spearman": round(float(r), 3), "p": float(f"{p:.2g}"),
                       "offline_mean": round(sum(xs) / len(xs), 2), "llm_mean": round(sum(ys) / len(ys), 2)}
    for f in ("hook_signal", "cliff_signal"):
        xs = [off_ann[b][f] for b in llm_only]
        ys = [llm_ann[b][f] for b in llm_only]
        if len(set(ys)) > 1:
            r, p = spearmanr(xs, ys)
            corr[f] = {"spearman": round(float(r), 3), "p": float(f"{p:.2g}"),
                       "offline_mean": round(sum(xs) / len(xs), 2), "llm_mean": round(sum(ys) / len(ys), 2)}
    out["beat_score_agreement"] = corr
    cat_j = []
    for b in llm_only:
        A, B = set(off_ann[b]["categories"]), set(llm_ann[b]["categories"])
        if A | B:
            cat_j.append(len(A & B) / len(A | B))
    out["category_jaccard_mean"] = round(sum(cat_j) / max(1, len(cat_j)), 3)
    # quote audit straight from the unedited raw responses
    import re as _re
    from src.dramatic.features import _CompactBatch, _row_to_beat
    from src.llm import extract_json
    tl = load(a.offline / "processed/episode_timelines.json")
    btext = {b["beat_id"]: b["text"] for e in tl for b in e["beats"]}
    norm = lambda s: _re.sub(r"[^a-z0-9 ]", "", s.lower())
    audit = {"exact": 0, "normalised_match": 0, "not_in_beat": 0, "no_quote_given": 0, "examples_not_in_beat": []}
    raw = a.llm / "raw/llm_responses.jsonl"
    for line in raw.open() if raw.exists() else []:
        r = json.loads(line)
        if not r["task"].startswith("annotate_batch"):
            continue
        try:
            rows = _CompactBatch.model_validate(extract_json(r["response"])).beats
        except Exception:
            continue
        for row in rows:
            x = _row_to_beat(row)
            if x is None or x.beat_id not in btext:
                continue
            q = x.evidence_quote.strip().strip('"')
            if not q:
                audit["no_quote_given"] += 1
            elif q.lower()[:60] in btext[x.beat_id].lower():
                audit["exact"] += 1
            elif norm(q) in norm(btext[x.beat_id]):
                audit["normalised_match"] += 1
            else:
                audit["not_in_beat"] += 1
                if len(audit["examples_not_in_beat"]) < 5:
                    audit["examples_not_in_beat"].append(x.beat_id)
    out["quote_audit"] = audit

    om, lm = load(a.offline / "final/run_metadata.json"), load(a.llm / "final/run_metadata.json")
    out["run"] = {k: {"offline": om.get(k), "llm": lm.get(k)} for k in
                  ("validated", "rejected", "reject_reasons", "total_s", "models", "llm_judging")}
    out["llm_usage"] = lm["usage"]

    orank, lrank = load(a.offline / "final/ranked_promos.json")["promos"], load(a.llm / "final/ranked_promos.json")["promos"]
    matches = []
    for r in lrank:
        best = max(((interval_overlap(r, o), o["promo_id"]) for o in orank), default=(0, None))
        matches.append({"llm_promo": r["promo_id"], "episodes": r["episodes"], "theme": r["theme"],
                        "best_offline_match": best[1] if best[0] > 0.3 else None, "overlap": round(best[0], 2)})
    out["shortlist_overlap"] = {"llm_promos_matching_an_offline_pick": sum(1 for m in matches if m["best_offline_match"]),
                                "of": len(lrank), "detail": matches,
                                "episodes_offline": sorted({e for r in orank for e in r["episodes"]}),
                                "episodes_llm": sorted({e for r in lrank for e in r["episodes"]})}

    ev = load(a.llm / "raw/evaluation_results.json")
    judged = [e for e in ev.values() if e.get("judged_by", "heuristic") != "heuristic"]
    out["llm_judges"] = {
        "judged": len(judged),
        "standalone_false": sum(1 for e in judged if not e["cold_viewer"]["standalone"]),
        "mean_llm_clarity": round(sum(e["cold_viewer"]["clarity_score"] for e in judged) / max(1, len(judged)), 2),
        "answered_inside_promo": sum(1 for e in judged if e["integrity"]["answered_inside_promo"]),
        "not_faithful_to_source": sum(1 for e in judged if not e["integrity"]["faithful_to_source"]),
        "rejected_after_llm_judging": sum(1 for e in judged if e["gate_failures"]),
        "gate_failures": dict(Counter(r.split(" (")[0] for e in judged for r in e["gate_failures"])),
        "example_missing_information": [m for e in judged for m in e["cold_viewer"]["missing_information"]][:12],
    }
    dest = ROOT / "evaluation" / "results" / "compare_offline_vs_llm.json"
    dest.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
