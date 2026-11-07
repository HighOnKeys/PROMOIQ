"""Benchmark PROMOIQ against baselines with one set of metrics.

    python -m evaluation.benchmark

Writes
  evaluation/results/benchmark.json          aggregate + per-clip metrics (times only, no script text)
  evaluation/results/benchmark_table.md      the comparison table
  outputs/evaluation/blind_review_sheet.md   18 clips, shuffled, system labels hidden (CONTAINS SCRIPT TEXT, private)
  outputs/evaluation/blind_key.json          blind id -> system (do not open before rating)
  outputs/evaluation/pairwise_sheet.md       6 blind A/B pairs

Honesty note: hook / cliffhanger / clarity pass rates are measured with PROMOIQ's own
scorers, which PROMOIQ optimises, so PROMOIQ's advantage on those rows is partly by
construction. Duration, timestamp validity, mid-line cuts and duplicates are objective.
"""
from __future__ import annotations

import json
import logging
import random
import time
from itertools import combinations
from pathlib import Path

from config import ROOT, get_settings
from evaluation.baselines import Clip, baseline_a0_keyword, baseline_a_llm, baseline_b_event_rank
from src.construction.scoring import clarity, coherence, spoilers
from src.evaluation.validator import source_times
from src.llm import make_gateway
from src.pipeline import run

log = logging.getLogger("benchmark")
RES = ROOT / "evaluation" / "results"
PRIV = ROOT / "outputs" / "evaluation"


def _beat_range(show, ep, a, b):
    tl = show.timelines[ep]
    return (ep, tl.beat_at(a), tl.beat_at(max(a, b - 1e-3)))


def clip_metrics(show, clip: Clip, times, blockers, st) -> dict:
    c = st.constraints
    d = clip.duration
    segs_ok = all(ep in show.timelines and 0 <= a < b <= show.episodes[ep].duration + 1e-6 for ep, a, b in clip.segments)
    m = {"clip_id": clip.clip_id, "system": clip.system, "duration": d,
         "segments": [(ep, round(a, 2), round(b, 2)) for ep, a, b in clip.segments]}
    m["duration_ok"] = c.min_duration - 1e-6 <= d <= c.max_duration + 1e-6
    m["valid_refs"] = segs_ok
    if not segs_ok:
        m.update(timestamps_are_source=False, no_midline_cut=False, hook=0, cliff=0, clarity=0, coherence=0, spoiler=0)
        return m
    m["timestamps_are_source"] = all(round(a, 3) in times[ep] and round(b, 3) in times[ep] for ep, a, b in clip.segments)
    cuts = [e.event_id for ep, a, b in clip.segments for e in blockers.get(ep, [])
            for t in (a, b) if e.start < t - 0.05 and e.end > t + 0.05]
    m["no_midline_cut"] = not cuts
    m["midline_cuts"] = len(cuts)
    ep0, a0, _ = clip.segments[0]
    epL, _, bL = clip.segments[-1]
    m["hook"] = show.timelines[ep0].hook_at_time(a0)[0]
    m["cliff"] = show.timelines[epL].cliff_at_time(bL)[0]
    br = tuple(_beat_range(show, ep, a, b) for ep, a, b in clip.segments)
    m["clarity"] = round(clarity(show, br)[0], 2)
    m["coherence"] = round(coherence(show, br)[0], 2)
    m["spoiler"] = round(spoilers(show, br)[0], 2)
    return m


def overlap(a: Clip, b: Clip) -> float:
    ov = sum(max(0.0, min(y1, y2) - max(x1, x2)) for e1, x1, y1 in a.segments for e2, x2, y2 in b.segments if e1 == e2)
    return ov / max(1e-6, min(a.duration, b.duration))


def same_scene(show, a: Clip, b: Clip) -> bool:
    def scenes(c):
        out = set()
        for ep, x, y in c.segments:
            tl = show.timelines.get(ep)
            if tl is None:
                continue
            for k in range(tl.beat_at(x), tl.beat_at(max(x, y - 1e-3)) + 1):
                out.add((ep, tl.beats[k].scene))
        return out
    return bool(scenes(a) & scenes(b))


def aggregate(show, clips: list[Clip], metrics: list[dict], seconds: float, calls: int, st) -> dict:
    n = len(metrics) or 1
    g = st.gates
    dup = 0
    for j in range(len(clips)):
        if any(overlap(clips[i], clips[j]) > 0.2 or same_scene(show, clips[i], clips[j]) for i in range(j)):
            dup += 1
    rate = lambda f: round(100 * sum(1 for m in metrics if f(m)) / n, 1)
    mean = lambda k: round(sum(m.get(k, 0) for m in metrics) / n, 2)
    return {
        "n": len(metrics),
        "duration_compliance_%": rate(lambda m: m["duration_ok"]),
        "timestamps_are_source_%": rate(lambda m: m.get("timestamps_are_source")),
        "no_midline_cut_%": rate(lambda m: m.get("no_midline_cut")),
        "hook_pass_%": rate(lambda m: m["hook"] >= g.hook_pass),
        "cliffhanger_pass_%": rate(lambda m: m["cliff"] >= g.cliffhanger_pass),
        "standalone_pass_%": rate(lambda m: m["clarity"] >= g.clarity_pass),
        "mean_hook": mean("hook"), "mean_cliff": mean("cliff"), "mean_clarity": mean("clarity"),
        "mean_coherence": mean("coherence"),
        "spoiler_flag_%": rate(lambda m: m["spoiler"] >= 4),
        "near_duplicate_%": round(100 * dup / n, 1),
        "processing_s": round(seconds, 2), "model_calls": calls, "approx_cost_usd": 0.0 if calls == 0 else None,
    }


def clip_script(show, clip: Clip) -> str:
    lines, off = [], 0.0
    for k, (ep, a, b) in enumerate(clip.segments, 1):
        lines.append(f"  -- segment {k} --")
        tl = show.timelines[ep]
        for e in tl.ep_events:
            if e.end <= a + 0.05 or e.start >= b - 0.05:
                continue
            tag = (f"{e.speaker}" + (f" ({', '.join(e.delivery)})" if e.delivery else "")) if e.event_type == "dialogue" else f"[{e.event_type.upper()}]"
            cut = " (cut mid-line)" if e.event_type == "dialogue" and (e.start < a - 0.05 or e.end > b + 0.05) else ""
            lines.append(f"  {max(0.0, e.start - a) + off:5.1f}s {tag}: {e.text}{cut}")
        off += b - a
    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    st = get_settings()
    RES.mkdir(parents=True, exist_ok=True)
    PRIV.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    res = run(settings=st)
    t_promoiq = time.time() - t0
    show = res["show"]
    times = source_times(show)
    blockers = {}
    for e in show.events.values():
        if e.event_type in ("dialogue", "sound"):
            blockers.setdefault(e.episode, []).append(e)

    systems: dict[str, tuple[list[Clip], float, int]] = {}
    promo = [Clip(system="PROMOIQ", clip_id=r["promo_id"],
                  segments=[(s["episode"], s["start_s"], s["end_s"]) for s in r["segments"]],
                  hook=r["hook"], cliffhanger=r["cliffhanger"], why=r["why_it_works"]) for r in res["ranked"]]
    systems["PROMOIQ"] = (promo, t_promoiq, res["meta"]["usage"]["generator"].get("calls", 0))
    t = time.time()
    systems["A0_keyword_window"] = (baseline_a0_keyword(show), time.time() - t, 0)
    t = time.time()
    systems["B_event_rank"] = (baseline_b_event_rank(show), time.time() - t, 0)
    gw = make_gateway(st.model.provider, st.model.model, st.model.cache_dir, st.paths.raw / "llm_responses.jsonl")
    a_status = "not run: no LLM provider configured (set PROMOIQ_PROVIDER and an API key)"
    if gw is not None:
        from src.parser import find_episode_files
        texts = {i + 1: p.read_text() for i, p in enumerate(find_episode_files(st.paths.input_dir))}
        t = time.time()
        clips = baseline_a_llm(show, gw, texts)
        systems["A_llm_only"] = (clips[:10], time.time() - t, gw.usage.calls)
        a_status = f"ran with {gw.name}"

    report = {"baseline_A_llm_only": a_status, "systems": {}, "per_clip": {}}
    for name, (clips, secs, calls) in systems.items():
        ms = [clip_metrics(show, c, times, blockers, st) for c in clips]
        report["systems"][name] = aggregate(show, clips, ms, secs, calls, st)
        report["per_clip"][name] = ms
    (RES / "benchmark.json").write_text(json.dumps(report, indent=2))

    rows = ["duration_compliance_%", "timestamps_are_source_%", "no_midline_cut_%", "hook_pass_%",
            "cliffhanger_pass_%", "standalone_pass_%", "mean_coherence", "spoiler_flag_%", "near_duplicate_%",
            "processing_s", "model_calls"]
    names = list(report["systems"])
    md = ["| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for r in rows:
        md.append(f"| {r} | " + " | ".join(str(report["systems"][n][r]) for n in names) + " |")
    md.append(f"\nBaseline A (LLM-only): {a_status}.")
    (RES / "benchmark_table.md").write_text("\n".join(md))
    print("\n".join(md))

    # -------- blind review sheet (private: contains screenplay text)
    rng = random.Random(st.search.seed)
    pool = ([(c, "PROMOIQ") for c in promo[:8]] + [(c, "B_event_rank") for c in systems["B_event_rank"][0][:5]]
            + [(c, "A0_keyword_window") for c in systems["A0_keyword_window"][0][:5]])
    rng.shuffle(pool)
    key, sheet = {}, ["# Blind promo review sheet", "",
                      "Rate each clip 1-5 on: Hook (first 3 s), Cliffhanger, Standalone clarity, Emotional appeal, "
                      "Narrative coherence. Also note timestamp problems (e.g. 'cut mid-line'), duration and spoiler risk.",
                      "System labels are hidden. CONFIDENTIAL: contains Kuku screenplay text.", ""]
    for n, (c, sysname) in enumerate(pool, 1):
        rid = f"R{n:02d}"
        key[rid] = {"system": sysname, "clip_id": c.clip_id}
        segs = " + ".join(f"E{ep} {a:.2f}s-{b:.2f}s" for ep, a, b in c.segments)
        sheet += [f"## {rid}  ({c.duration:.1f}s; {segs})", "```", clip_script(show, c), "```", ""]
    (PRIV / "blind_review_sheet.md").write_text("\n".join(sheet))
    (PRIV / "blind_key.json").write_text(json.dumps(key, indent=2))
    pairs, pkey = ["# Blind pairwise comparison",
                   "Question: Which promo would you recommend publishing first to a viewer who has never watched the show, and why?", ""], {}
    bsys = systems["B_event_rank"][0]
    asys = systems["A0_keyword_window"][0]
    plan = [(promo[k], bsys[k]) for k in range(3)] + [(promo[k], asys[k]) for k in range(3)]
    for n, (p, other) in enumerate(plan, 1):
        x, y = (p, other) if rng.random() < 0.5 else (other, p)
        pkey[f"P{n}"] = {"X": x.system + ":" + x.clip_id, "Y": y.system + ":" + y.clip_id}
        pairs += [f"## P{n}", "### X", "```", clip_script(show, x), "```", "### Y", "```", clip_script(show, y), "```", ""]
    (PRIV / "pairwise_sheet.md").write_text("\n".join(pairs))
    (PRIV / "pairwise_key.json").write_text(json.dumps(pkey, indent=2))
    log.info("blind sheets written to %s", PRIV)


if __name__ == "__main__":
    main()
