"""PROMOIQ end-to-end pipeline.

    python -m src.pipeline --input data/input            # folder of episode .txt files
    python -m src.pipeline --input path/to/show.zip      # or a ZIP
    PROMOIQ_PROVIDER=gemini GEMINI_API_KEY=... python -m src.pipeline ...

Stages: parse -> dramatic features -> narrative bible/graph -> candidate search
(continuous + beam) -> hard validation -> judges -> gates -> MMR shortlist -> outputs.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import re
import shutil
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from config import Settings, get_settings
from src.construction.scoring import score
from src.construction.search import SegmentSimilarity, beam_search, continuous_candidates, materialise
from src.construction.show import Show
from src.dramatic.features import HeuristicAnnotator, LLMAnnotator, event_signal
from src.dramatic.timeline import EpisodeTimeline
from src.evaluation.judges import ColdViewerJudge, IntegrityJudge, _segs
from src.evaluation.validator import HardValidator
from src.llm import make_gateway
from src.narrative.story import (build_characters, build_conflicts, build_graph, extract_relationships,
                                 graph_to_json, llm_enrich)
from src.parser import build_beats, canonicalise, find_episode_files, parse_episode, validation_report
from src.ranking.portfolio import PromoSimilarity, counterfactuals, mmr_shortlist

log = logging.getLogger("promoiq")


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=lambda o: sorted(o) if isinstance(o, set) else str(o))


def resolve_input(inp: Path, work: Path) -> tuple[Path, str]:
    """Accept a folder or a ZIP. Returns (folder, show title)."""
    inp = Path(inp)
    if inp.is_file() and inp.suffix.lower() == ".zip":
        dest = work / "_extracted" / re.sub(r"\W+", "_", inp.stem)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        with zipfile.ZipFile(inp) as z:
            for m in z.infolist():                      # zip-slip safe extraction
                target = (dest / m.filename).resolve()
                if not str(target).startswith(str(dest.resolve())):
                    raise ValueError(f"unsafe path in zip: {m.filename}")
            z.extractall(dest)
        inp = dest
    files = find_episode_files(inp)
    if not files:
        raise FileNotFoundError(f"no episode .txt files under {inp}")
    title = files[0].parent.name
    title = re.sub(r"-scripts.*$", "", title).strip() or "Untitled show"
    return inp, title


def run(input_path: Optional[Path] = None, settings: Optional[Settings] = None,
        progress: Callable[[str, float], None] = lambda s, f: None, out_root: Optional[Path] = None) -> dict:
    st = settings or get_settings()
    random.seed(st.search.seed)
    np.random.seed(st.search.seed)
    if out_root is not None:
        st.paths.outputs = Path(out_root)
    raw, proc, final = st.paths.raw, st.paths.processed, st.paths.final
    for d in (raw, proc, final):
        d.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    t0 = time.time()

    def mark(name):
        nonlocal t0
        timings[name] = round(time.time() - t0, 2)
        t0 = time.time()

    # ------------------------------------------------------------ models
    gen_gw = make_gateway(st.model.provider, st.model.model, st.model.cache_dir, raw / "llm_responses.jsonl",
                          st.model.temperature, st.model.max_retries)
    judge_gw = make_gateway(st.model.judge_provider or st.model.provider, st.model.judge_model or st.model.model,
                            st.model.cache_dir, raw / "llm_responses.jsonl", 0.0, st.model.max_retries)
    log.info("generator model: %s | judge model: %s", gen_gw.name if gen_gw else "heuristic",
             judge_gw.name if judge_gw else "heuristic")

    # ------------------------------------------------------------ stage 1: parse
    progress("Parsing screenplays", 0.02)
    folder, title = resolve_input(Path(input_path or st.paths.input_dir), st.paths.outputs.parent / "data")
    files = find_episode_files(folder)
    episodes, events, issues = [], [], []
    for n, f in enumerate(files, 1):
        ep, evs, iss = parse_episode(f, n)
        episodes.append(ep)
        events.extend(evs)
        issues.extend(iss)
    canonicalise(events)
    ev_by_ep = defaultdict(list)
    for e in events:
        ev_by_ep[e.episode].append(e)
    beats_by_ep = {ep.episode: build_beats(ep, ev_by_ep[ep.episode]) for ep in episodes}
    all_beats = [b for bl in beats_by_ep.values() for b in bl]
    report = validation_report(episodes, events, all_beats, issues)
    _dump(raw / "parsed_events.json", [e.model_dump() for e in events])
    _dump(proc / "parser_validation_report.json", report)
    _dump(proc / "episode_timelines.json", [{**ep.model_dump(), "beats": [b.model_dump() for b in beats_by_ep[ep.episode]]}
                                           for ep in episodes])
    mark("parse")

    # ------------------------------------------------------------ stage 3: dramatic features
    progress("Scoring dramatic features", 0.15)
    events_by_id = {e.event_id: e for e in events}
    signals = {e.event_id: event_signal(e) for e in events}
    heur = HeuristicAnnotator()
    annotator = LLMAnnotator(gen_gw, heur) if gen_gw else heur
    ann = {}
    prev_logs = {ep.episode: (episodes[i - 1].logline if i else "") for i, ep in enumerate(episodes)}
    epc = max(1, st.model.episodes_per_call)
    if isinstance(annotator, LLMAnnotator) and epc > 1:
        for k in range(0, len(episodes), epc):
            chunk = episodes[k:k + epc]
            res = annotator.annotate_batch([(ep, beats_by_ep[ep.episode], prev_logs[ep.episode]) for ep in chunk],
                                           events_by_id, signals)
            ann.update({a.beat_id: a for a in res})
            progress(f"LLM scoring episodes {chunk[0].episode}-{chunk[-1].episode}",
                     0.15 + 0.25 * min(1.0, (k + epc) / len(episodes)))
    else:
        for k, ep in enumerate(episodes):
            if isinstance(annotator, LLMAnnotator):
                res = annotator.annotate_episode(ep, beats_by_ep[ep.episode], events_by_id, signals, prev_logs[ep.episode])
            else:
                res = annotator.annotate_episode(ep, beats_by_ep[ep.episode], events_by_id, signals)
            ann.update({a.beat_id: a for a in res})
            progress("Scoring dramatic features", 0.15 + 0.25 * (k + 1) / len(episodes))
    timelines = {ep.episode: EpisodeTimeline(ep, beats_by_ep[ep.episode], ann, events_by_id, signals, st.weights,
                                             st.constraints.hook_window, st.constraints.cliff_window)
                 for ep in episodes}
    T = {b.beat_id: tl.T[i] for tl in timelines.values() for i, b in enumerate(tl.beats)}
    _dump(raw / "dramatic_events.json", [a.model_dump() for a in ann.values()])
    _dump(proc / "tension_profiles.json", [tl.profile() for tl in timelines.values()])
    hooks = sorted(((tl.hook_at[i], b, tl.hook_note[i]) for tl in timelines.values() for i, b in enumerate(tl.beats)),
                   key=lambda x: -x[0])[:150]
    cliffs = sorted(((tl.cliff_at[i], b, tl.cliff_note[i]) for tl in timelines.values() for i, b in enumerate(tl.beats)),
                    key=lambda x: -x[0])[:150]
    _dump(proc / "hook_candidates.json", [{"beat_id": b.beat_id, "episode": b.episode, "start": b.start_raw,
                                           "hook_strength": h, "evidence": n} for h, b, n in hooks])
    _dump(proc / "cliffhanger_candidates.json", [{"beat_id": b.beat_id, "episode": b.episode, "end": b.end_raw,
                                                  "cliff_strength": h, "evidence": n} for h, b, n in cliffs])
    mark("dramatic")

    # ------------------------------------------------------------ stage 2: narrative
    progress("Building story bible and narrative graph", 0.42)
    characters = build_characters(episodes, events)
    relationships = extract_relationships(episodes, all_beats, events_by_id, characters)
    llm_bible = llm_enrich(gen_gw, episodes, characters)
    if llm_bible and llm_bible.aliases:
        # apply model-asserted aliases (kept separately labelled), then rebuild character index
        canonicalise(events, {k.upper(): v.upper() for k, v in llm_bible.aliases.items()})
        characters = build_characters(episodes, events)
    conflicts = build_conflicts(all_beats, ann, T, characters, events_by_id)
    G = build_graph(characters, relationships, conflicts, all_beats, ann, T)
    bible = {
        "title": title,
        "premise_from_title": title,
        "episodes": [{"episode": e.episode, "logline": e.logline, "continuity": e.continuity,
                      "duration_s": e.duration} for e in episodes],
        "characters": characters,
        "relationships": relationships,
        "llm_assertions": llm_bible.model_dump() if llm_bible else None,
        "narrative_events": [
            {"beat_id": b.beat_id, "episode": b.episode, "start": b.start_raw, "end": b.end_raw,
             "categories": ann[b.beat_id].categories, "tension": round(T[b.beat_id], 2),
             "emotional_significance": round(ann[b.beat_id].scores.get("emotional_intensity", 0), 2),
             "narrative_importance": round(ann[b.beat_id].scores.get("narrative_importance", 0), 2),
             "characters": b.characters, "evidence": b.text[:240]}
            for b in sorted(all_beats, key=lambda b: -T[b.beat_id])[:120]
        ],
        "notes": [
            "Episode numbering is used as story order. Some episodes re-tell or overlap earlier events "
            "(e.g. the altar scene appears in E1 and E4); retellings are caught by semantic redundancy checks.",
            "Descriptive speaker labels (e.g. 'LADY IN GOLD GOWN') are kept as separate characters offline; "
            "an LLM provider can resolve them via llm_assertions.aliases.",
        ],
    }
    _dump(proc / "story_bible.json", bible)
    _dump(proc / "character_index.json", characters)
    _dump(proc / "conflict_timeline.json", conflicts)
    _dump(proc / "narrative_graph.json", graph_to_json(G))
    mark("narrative")

    # ------------------------------------------------------------ stage 4: search
    progress("Searching promo sequences", 0.50)
    show = Show(title=title, episodes={e.episode: e for e in episodes}, events=events_by_id,
                timelines=timelines, characters=characters, conflicts=conflicts)
    seg_sim = SegmentSimilarity(show, st.ranking.embedding_model)
    cont = continuous_candidates(show, st, seg_sim)
    mark("search_continuous")
    progress("Beam search for multi-segment promos", 0.60)
    multi = beam_search(show, st, seg_sim)[:400]
    mark("search_beam")
    cands = []
    for q, segs in cont:
        cands.append((segs, materialise(show, segs, score(show, segs, st.weights, seg_sim), "continuous")))
    for q, segs in multi:
        cands.append((segs, materialise(show, segs, score(show, segs, st.weights, seg_sim), "multi_segment")))
    segs_of = {c.promo_id: s for s, c in cands}
    cand_list = [c for _, c in cands]
    _dump(raw / "promo_candidates.json", [c.model_dump() for c in cand_list])
    mark("materialise")

    # ------------------------------------------------------------ stage 5: evaluation
    progress("Validating and judging candidates", 0.70)
    validator = HardValidator(show, st.constraints)
    cold = ColdViewerJudge(show, None)
    integ = IntegrityJudge(show, None)
    cold_llm = ColdViewerJudge(show, judge_gw) if judge_gw else None
    integ_llm = IntegrityJudge(show, judge_gw) if judge_gw else None
    evals, validated, rejected = {}, [], []
    ranked_by_q = sorted(cand_list, key=lambda c: -c.scores.quality)
    hard = {c.promo_id: validator.validate(c) for c in ranked_by_q}
    # LLM judges are spent on DISTINCT strong candidates only: valid, past the cheap hook and
    # cliffhanger gates, and not a boundary variant (>50% time overlap) of one already chosen.
    llm_set: list = []
    if cold_llm is not None:
        from src.ranking.portfolio import time_overlap
        for c in ranked_by_q:
            if len(llm_set) >= st.model.judge_budget:
                break
            if not hard[c.promo_id]["valid"] or c.scores.hook < st.gates.hook_pass \
                    or c.scores.cliffhanger < st.gates.cliffhanger_pass:
                continue
            if any(time_overlap(c, x) > 0.5 for x in llm_set):
                continue
            llm_set.append(c)
    llm_ids = {c.promo_id for c in llm_set}
    llm_judged: set = set()
    for n, c in enumerate(ranked_by_q):
        v = hard[c.promo_id]
        use_llm = c.promo_id in llm_ids
        if use_llm:
            progress(f"LLM judging {len(llm_judged) + 1}/{len(llm_ids)}", 0.70 + 0.15 * len(llm_judged) / max(1, len(llm_ids)))
        cv = (cold_llm if use_llm else cold).judge(c)
        ig = (integ_llm if use_llm else integ).judge(c)
        # count it as LLM-judged only if both judges actually answered (they fall back on errors)
        use_llm = use_llm and not cv.judge.startswith("heuristic") and not ig.judge.startswith("heuristic")
        if use_llm:
            llm_judged.add(c.promo_id)
        reasons = [f"hard:{f}" for f in v["failed"]]
        g = st.gates
        if c.scores.hook < g.hook_pass:
            reasons.append(f"weak hook ({c.scores.hook:.2f} < {g.hook_pass})")
        if c.scores.cliffhanger < g.cliffhanger_pass:
            reasons.append(f"weak cliffhanger ({c.scores.cliffhanger:.2f} < {g.cliffhanger_pass})")
        # the brief requires a cliffhanger: if the integrity judge says the ending leaves no open question,
        # a high generator score must not outweigh that verdict. (The heuristic judge derives this verdict
        # from the same cliffhanger score, so only report it when the score gate did not already fire.)
        elif not ig.ending_leaves_question:
            reasons.append("ending leaves no open question (integrity judge)")
        if cv.clarity_score < g.clarity_pass:
            reasons.append(f"not standalone ({cv.clarity_score:.2f} < {g.clarity_pass}): " + "; ".join(cv.missing_information[:2]))
        if c.mode == "multi_segment" and c.scores.coherence < g.multi_coherence_pass:
            reasons.append(f"incoherent multi-segment join ({c.scores.coherence:.2f} < {g.multi_coherence_pass})")
        if ig.spoiler_risk > g.max_spoiler:
            reasons.append(f"spoiler risk {ig.spoiler_risk:.1f} ({', '.join(ig.spoiler_types)})")
        final_score = c.scores.quality - (0.4 if ig.answered_inside_promo else 0.0) - (0.3 if not ig.hook_connected_to_ending else 0.0)
        if use_llm:
            final_score = 0.6 * final_score + 0.4 * (cv.clarity_score + ig.cliffhanger_score + ig.coherence_score) / 3
        evals[c.promo_id] = {"validation": v, "cold_viewer": cv.model_dump(), "integrity": ig.model_dump(),
                             "final_score": round(final_score, 3), "gate_failures": reasons,
                             "judged_by": cv.judge if use_llm else "heuristic"}
        (rejected if reasons else validated).append(c)
    _dump(raw / "evaluation_results.json", evals)
    _dump(proc / "validated_promos.json", [{**c.model_dump(), "final_score": evals[c.promo_id]["final_score"]} for c in validated])
    _dump(proc / "rejected_candidates.json", [{"promo_id": c.promo_id, "mode": c.mode,
                                               "segments": [s.model_dump() for s in c.segments], "duration": c.duration,
                                               "scores": c.scores.model_dump(), "reasons": evals[c.promo_id]["gate_failures"]}
                                              for c in rejected])
    mark("evaluate")

    # ------------------------------------------------------------ stage 6: ranking
    progress("Diversity-aware ranking", 0.88)
    psim = PromoSimilarity(show, seg_sim, lambda p: segs_of[p.promo_id])
    quality = {c.promo_id: evals[c.promo_id]["final_score"] for c in validated}
    # With LLM judges, LLM-blended and heuristic-only scores are on different scales: rank only
    # within the LLM-judged pool (it already holds the strongest distinct candidates).
    pool = [c for c in validated if c.promo_id in llm_judged] if llm_judged else validated
    if llm_judged and len(pool) < st.ranking.shortlist_size:
        log.warning("only %d LLM-judged candidates passed the gates; shortlist will be shorter", len(pool))
    shortlist, dup_log = mmr_shortlist(pool, quality, psim, st.ranking.shortlist_size, st.ranking.mmr_lambda,
                                       st.ranking.min_quality_ratio, st.ranking.duplicate_threshold)
    # MMR decides WHO is in the shortlist; within it, post order follows quality.
    mmr_order = {c.promo_id: k for k, c in enumerate(shortlist, 1)}
    shortlist = sorted(shortlist, key=lambda c: -quality[c.promo_id])
    ranked = []
    for rank, c in enumerate(shortlist, 1):
        e = evals[c.promo_id]
        cf = counterfactuals(show, segs_of[c.promo_id], st.weights, seg_sim, st.constraints)
        alts = [d for d in dup_log if d["similar_to"] == c.promo_id][:5]
        margin = min(c.scores.hook - st.gates.hook_pass, c.scores.cliffhanger - st.gates.cliffhanger_pass,
                     e["cold_viewer"]["clarity_score"] - st.gates.clarity_pass)
        confidence = "high" if margin >= 1.5 and not c.flags else ("medium" if margin >= 0.5 else "low")
        limits = ["Timestamps come from the screenplay; verify cut points against the edited episode."]
        if e["cold_viewer"]["missing_information"]:
            limits.append("Cold-viewer gaps: " + "; ".join(e["cold_viewer"]["missing_information"][:3]))
        if c.flags:
            limits.append("Spoiler flags: " + ", ".join(c.flags))
        if any(x["valid"] and x["delta"] > 0 for x in cf):
            limits.append("A boundary variant scored higher but was not selected (overlaps a stronger pick).")
        ranked.append({
            "rank": rank,
            "promo_id": f"PROMO-{rank:02d}",
            "candidate_id": c.promo_id,
            "mmr_pick_order": mmr_order[c.promo_id],
            "theme": c.theme,
            "mode": c.mode,
            "episodes": sorted({s.episode for s in c.segments}),
            "segments": [{"order": s.order, "episode": s.episode, "start": s.start_raw, "end": s.end_raw,
                          "start_s": s.start, "end_s": s.end, "duration_s": s.duration} for s in c.segments],
            "total_duration_s": c.duration,
            "hook": c.hook,
            "cliffhanger": c.cliffhanger,
            "why_it_works": c.why,
            "characters": c.characters,
            "scores": c.scores.model_dump(),
            "final_score": e["final_score"],
            "cold_viewer": e["cold_viewer"],
            "integrity": e["integrity"],
            "evidence": c.evidence,
            "boundary_counterfactuals": cf,
            "near_duplicates_flagged": alts,
            "confidence": confidence,
            "limitations": limits,
        })
    # Strong candidates the diversity pass held back: shown to the editor, not hidden.
    floor = min((quality[c.promo_id] for c in shortlist), default=0.0)
    chosen = {c.promo_id for c in shortlist}
    alternatives = []
    for c in sorted(pool, key=lambda c: -quality[c.promo_id]):
        if c.promo_id in chosen or quality[c.promo_id] < floor:
            continue
        sims = sorted(((psim.detail(c, s), s) for s in shortlist), key=lambda x: -x[0]["combined"])
        d, s = sims[0]
        if d["time_overlap"] > 0.3 or d["shared_beats"] > 0.3:
            continue          # a boundary variant of a pick, not a different promo
        if any(a["_obj"] is not None and psim.detail(c, a["_obj"])["time_overlap"] > 0.3 for a in alternatives):
            continue          # a boundary variant of an alternative already listed
        alternatives.append({
            "candidate_id": c.promo_id, "theme": c.theme, "mode": c.mode, "final_score": round(quality[c.promo_id], 3),
            "segments": [{"episode": x.episode, "start": x.start_raw, "end": x.end_raw} for x in c.segments],
            "duration_s": c.duration, "hook": c.hook, "cliffhanger": c.cliffhanger,
            "held_back_because": f"similar to {next(r['promo_id'] for r in ranked if r['candidate_id'] == s.promo_id)} "
                                 f"(semantic {d['semantic']:.2f}, same-scene {d['same_scene']:.2f})",
            "_obj": c,
        })
        if len(alternatives) >= 5:
            break
    for a in alternatives:
        a.pop("_obj", None)
    _dump(final / "ranked_promos.json", {"show": title, "generated_by": "PROMOIQ",
                                         "model": gen_gw.name if gen_gw else "heuristic (offline)",
                                         "promos": ranked, "strong_alternatives_held_back": alternatives})
    with (final / "ranked_promos.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "promo_id", "theme", "mode", "segments (episode start-end, in order)", "total_duration_s",
                    "hook", "cliffhanger", "why_it_works", "quality_Q", "final_score", "hook_H", "cliff_K",
                    "clarity_C", "confidence"])
        for r in ranked:
            segtxt = " | ".join(f"E{s['episode']:02d} {s['start']}-{s['end']}" for s in r["segments"])
            w.writerow([r["rank"], r["promo_id"], r["theme"], r["mode"], segtxt, r["total_duration_s"], r["hook"],
                        r["cliffhanger"], r["why_it_works"], r["scores"]["quality"], r["final_score"],
                        r["scores"]["hook"], r["scores"]["cliffhanger"], r["scores"]["clarity"], r["confidence"]])
    _dump(proc / "duplicate_flags.json", dup_log)
    mark("rank")

    usage = {"generator": gen_gw.usage.as_dict(gen_gw.real_tokens) if gen_gw else {"calls": 0, "note": "offline heuristic provider"},
             "judge": judge_gw.usage.as_dict(judge_gw.real_tokens) if judge_gw and judge_gw is not gen_gw else None}
    annot_sources = Counter(a.annotator for a in ann.values())
    reject_reasons = Counter(re.sub(r"\s[\d.]+$", "", r.split(" (")[0].split(":")[0]) if not r.startswith("hard:") else r
                             for c in rejected for r in evals[c.promo_id]["gate_failures"])
    meta = {
        "show": title, "input": str(folder), "episodes": len(episodes), "events": len(events), "beats": len(all_beats),
        "runtime_s_total_footage": report["total_runtime_s"],
        "candidates": {"continuous": len(cont), "multi_segment": len(multi), "total": len(cand_list)},
        "validated": len(validated), "rejected": len(rejected), "shortlist": len(ranked),
        "reject_reasons": dict(reject_reasons.most_common()),
        "duplicates_flagged": len(dup_log),
        "timings_s": timings, "total_s": round(sum(timings.values()), 2),
        "models": {"generator": gen_gw.name if gen_gw else "heuristic", "judge": judge_gw.name if judge_gw else "heuristic",
                   "embeddings": seg_sim.backend},
        "usage": usage,
        "unverified_llm_quotes": getattr(annotator, "unverified_quotes", []),
        "beat_annotation_sources": dict(annot_sources),
        "llm_judging": {"budget": st.model.judge_budget if judge_gw else 0, "selected": len(llm_ids),
                        "judged_ok": len(llm_judged), "ranking_pool": len(pool)},
        "settings": {"weights": vars(st.weights), "search": vars(st.search), "gates": vars(st.gates),
                     "ranking": vars(st.ranking), "constraints": vars(st.constraints)},
    }
    _dump(final / "run_metadata.json", meta)
    from src.ranking.report import write_report
    write_report(final / "editorial_report.md", show, ranked, rejected, evals, meta, dup_log, alternatives)
    progress("Done", 1.0)
    log.info("done in %.1fs: %d candidates, %d validated, %d shortlisted", meta["total_s"], len(cand_list),
             len(validated), len(ranked))
    return {"meta": meta, "ranked": ranked, "show": show, "evals": evals, "candidates": cand_list,
            "rejected": rejected, "validated": validated, "segs_of": segs_of}


def main():
    ap = argparse.ArgumentParser(description="PROMOIQ promo discovery")
    ap.add_argument("--input", type=Path, default=None, help="folder of episode .txt files or a .zip")
    ap.add_argument("--out", type=Path, default=None, help="output root (default ./outputs)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    res = run(a.input, out_root=a.out, progress=lambda s, f: log.info("[%3d%%] %s", int(f * 100), s))
    for r in res["ranked"]:
        segs = " + ".join(f"E{s['episode']} {s['start']}-{s['end']}" for s in r["segments"])
        print(f"#{r['rank']:>2} {r['promo_id']} [{r['theme']}] {segs} ({r['total_duration_s']:.1f}s) "
              f"Q={r['scores']['quality']:.2f} final={r['final_score']:.2f}")


if __name__ == "__main__":
    main()
