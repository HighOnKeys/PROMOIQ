"""PROMOIQ editorial interface (local Streamlit app).

    streamlit run app.py

Runs locally on purpose: the screenplays are confidential and must not be hosted publicly.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from config import ROOT, get_settings
from src.evaluation.judges import promo_script
from src.pipeline import run

logging.basicConfig(level=logging.INFO)
st.set_page_config(page_title="PROMOIQ", page_icon="🎬", layout="wide")

LIMITATION = ("PROMOIQ analyses screenplay text only. It cannot verify actual video frames, acting performance, "
              "editing quality or audience engagement. Check every cut point against the edited episode.")

st.title("PROMOIQ · promo discovery for microdrama screenplays")
st.warning(LIMITATION)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("1 · Input")
    source = st.radio("Screenplays", ["Local folder (data/input)", "Upload ZIP or .txt files"])
    uploads = None
    if source.startswith("Upload"):
        uploads = st.file_uploader("Episode files", type=["zip", "txt"], accept_multiple_files=True)
    st.header("2 · Model")
    provider = st.selectbox("Provider", ["heuristic", "gemini", "anthropic", "openai_compat"],
                            help="heuristic = offline and free. Others read API keys from environment variables only.")
    model = st.text_input("Model name (optional)", value="")
    key_env = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai_compat": "OPENAI_API_KEY"}.get(provider)
    if key_env and not os.environ.get(key_env):
        st.error(f"{key_env} is not set in the environment; the run will fail. Use 'heuristic' or export the key.")
    st.header("3 · Weights")
    s = get_settings()
    w = s.weights
    w.hook = st.slider("Hook H", 0.0, 0.5, w.hook, 0.05)
    w.cliffhanger = st.slider("Cliffhanger K", 0.0, 0.5, w.cliffhanger, 0.05)
    w.clarity = st.slider("Standalone clarity C", 0.0, 0.5, w.clarity, 0.05)
    s.ranking.shortlist_size = st.slider("Shortlist size", 3, 20, s.ranking.shortlist_size)
    go = st.button("Run analysis", type="primary", use_container_width=True)


def _prepare_input() -> Path | None:
    if source.startswith("Local"):
        return s.paths.input_dir
    if not uploads:
        st.error("Upload a ZIP or episode .txt files first.")
        return None
    tmp = Path(tempfile.mkdtemp(prefix="promoiq_"))
    show_dir = tmp / "uploaded_show"
    show_dir.mkdir()
    for f in uploads:
        if f.name.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(f.getvalue())) as z:
                for m in z.infolist():
                    target = (show_dir / m.filename).resolve()
                    # is_relative_to, not a string prefix: ".../uploaded_show_evil" starts with ".../uploaded_show"
                    if not target.is_relative_to(show_dir.resolve()):
                        st.error(f"Unsafe path in ZIP: {m.filename}")
                        return None
                z.extractall(show_dir)
        else:
            (show_dir / Path(f.name).name).write_bytes(f.getvalue())
    return show_dir


if go:
    inp = _prepare_input()
    if inp is not None:
        s.model.provider = provider
        s.model.model = model
        out_root = ROOT / "outputs" / "app_run"
        bar = st.progress(0.0, text="Starting")
        try:
            res = run(inp, settings=s, out_root=out_root, progress=lambda m, f: bar.progress(min(1.0, f), text=m))
            st.session_state["res"] = res
            st.session_state["out_root"] = out_root
            bar.progress(1.0, text="Done")
        except Exception as exc:  # surface errors, never swallow them
            st.exception(exc)

res = st.session_state.get("res")
if not res:
    st.info("Choose an input and press **Run analysis**. The full 50-episode pack runs in about 20 seconds offline.")
    st.stop()

meta, show, ranked, evals = res["meta"], res["show"], res["ranked"], res["evals"]
cands = {c.promo_id: c for c in res["candidates"]}
out_root = st.session_state["out_root"]

tabs = st.tabs(["Overview", "Ranked promos", "Promo inspector", "Rejected", "Evaluation", "Export"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    c = st.columns(5)
    c[0].metric("Episodes", meta["episodes"])
    c[1].metric("Footage", f"{meta['runtime_s_total_footage'] / 60:.1f} min")
    c[2].metric("Candidates", meta["candidates"]["total"])
    c[3].metric("Validated", meta["validated"])
    c[4].metric("Run time", f"{meta['total_s']:.1f}s")
    st.caption(f"Models: generator={meta['models']['generator']}, judge={meta['models']['judge']}, "
               f"similarity={meta['models']['embeddings']}")
    left, right = st.columns(2)
    with left:
        st.subheader("Characters")
        chars = pd.DataFrame([{"name": v["name"], "role": v["role"], "lines": v["dialogue_lines"],
                               "episodes": f"{v['first_episode']}-{v['last_episode']}", "alias status": v["alias_status"]}
                              for v in show.characters.values()]).head(20)
        st.dataframe(chars, hide_index=True, use_container_width=True)
    with right:
        st.subheader("Conflicts (antagonistic pairs)")
        st.dataframe(pd.DataFrame([{"conflict": k.replace("|", " vs "), "events": v["size"],
                                    "episodes": f"{v['first_episode']}-{v['last_episode']}",
                                    "resolved in": v["resolved_in_episode"]} for k, v in show.conflicts.items()]),
                     hide_index=True, use_container_width=True)
    st.subheader("Tension curve")
    ep = st.selectbox("Episode", sorted(show.timelines), format_func=lambda e: f"Episode {e}")
    tl = show.timelines[ep]
    st.caption(show.episodes[ep].logline)
    df = pd.DataFrame({"t (s)": [b.start for b in tl.beats], "tension": tl.T_smooth,
                       "hook strength/2": [h / 2 for h in tl.hook_at], "cliffhanger strength/2": [k / 2 for k in tl.cliff_at]})
    st.line_chart(df, x="t (s)")

# ------------------------------------------------------------------ ranked
with tabs[1]:
    st.subheader("Post these first")
    rows = [{"rank": r["rank"], "promo": r["promo_id"], "theme": r["theme"], "mode": r["mode"],
             "segments": " + ".join(f"E{x['episode']} {x['start']}-{x['end']}" for x in r["segments"]),
             "duration (s)": r["total_duration_s"], "score": r["final_score"], "H": r["scores"]["hook"],
             "K": r["scores"]["cliffhanger"], "C": r["scores"]["clarity"], "confidence": r["confidence"]} for r in ranked]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    for r in ranked:
        with st.expander(f"#{r['rank']} {r['promo_id']} · {r['theme']} · {r['total_duration_s']:.1f}s"):
            st.markdown(f"**Hook:** {r['hook']}\n\n**Cliffhanger:** {r['cliffhanger']}\n\n**Why it works:** {r['why_it_works']}")
    alt = json.loads((out_root / "final" / "ranked_promos.json").read_text()).get("strong_alternatives_held_back", [])
    if alt:
        st.subheader("Strong alternatives held back for diversity")
        st.dataframe(pd.DataFrame([{"candidate": a["candidate_id"], "theme": a["theme"],
                                    "segments": " + ".join(f"E{x['episode']} {x['start']}-{x['end']}" for x in a["segments"]),
                                    "score": a["final_score"], "why held back": a["held_back_because"]} for a in alt]),
                     hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ inspector
with tabs[2]:
    pick = st.selectbox("Promo", [r["promo_id"] for r in ranked])
    r = next(x for x in ranked if x["promo_id"] == pick)
    cand = cands[r["candidate_id"]]
    st.markdown(f"### {r['promo_id']} · {r['theme']} · {r['mode']} · {r['total_duration_s']:.1f}s · confidence {r['confidence']}")
    st.dataframe(pd.DataFrame(r["segments"]), hide_index=True, use_container_width=True)
    a, b = st.columns([3, 2])
    with a:
        st.markdown("**What the viewer sees and hears (screenplay text, in order)**")
        st.code(promo_script(show, cand), language=None)
    with b:
        st.markdown("**Scores (0-10)**")
        sc = {k: v for k, v in r["scores"].items() if k != "quality"}
        st.bar_chart(pd.Series(sc))
        st.caption(f"Q = {r['scores']['quality']:.2f}; final = {r['final_score']:.2f}")
        st.markdown(f"**Hook (first 3s):** {r['hook']}")
        st.markdown(f"**Cliffhanger:** {r['cliffhanger']}")
        st.markdown(f"**Why:** {r['why_it_works']}")
    st.markdown("**Source evidence**")
    st.dataframe(pd.DataFrame(r["evidence"]), hide_index=True, use_container_width=True)
    v = evals[r["candidate_id"]]["validation"]
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Hard checks**")
        st.dataframe(pd.DataFrame(v["checks"]), hide_index=True, use_container_width=True)
    with c2:
        st.markdown("**Cold-viewer judge** (sees only the promo)")
        st.json(r["cold_viewer"])
    with c3:
        st.markdown("**Cliffhanger & integrity judge**")
        st.json(r["integrity"])
    st.markdown("**Boundary counterfactuals**")
    st.dataframe(pd.DataFrame(r["boundary_counterfactuals"]), hide_index=True, use_container_width=True)
    if r["limitations"]:
        st.info(" ".join(r["limitations"]))

# ------------------------------------------------------------------ rejected
with tabs[3]:
    rej = res["rejected"]
    st.caption(f"{len(rej)} candidates failed a hard check or an editorial gate. Reasons: {meta['reject_reasons']}")
    reason = st.selectbox("Filter by reason", ["all"] + list(meta["reject_reasons"]))
    rows = []
    for c in rej:
        rs = evals[c.promo_id]["gate_failures"]
        if reason != "all" and not any(x.startswith(reason.split(" (")[0]) for x in rs):
            continue
        rows.append({"candidate": c.promo_id, "mode": c.mode,
                     "segments": " + ".join(f"E{x.episode} {x.start_raw}-{x.end_raw}" for x in c.segments),
                     "Q": c.scores.quality, "H": c.scores.hook, "K": c.scores.cliffhanger, "reasons": "; ".join(rs)})
    st.dataframe(pd.DataFrame(rows).sort_values("Q", ascending=False).head(300) if rows else pd.DataFrame(),
                 hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ evaluation
with tabs[4]:
    bench = ROOT / "evaluation" / "results" / "benchmark_table.md"
    if bench.exists():
        st.markdown("**Baseline comparison** (from `python -m evaluation.benchmark`)")
        st.markdown(bench.read_text())
    rs = ROOT / "evaluation" / "results" / "round1" / "review_summary_ai_review_ratings.json"
    if rs.exists():
        st.markdown("**Blind review round 1** (AI assistant reviewer, not human)")
        st.json(json.loads(rs.read_text())["systems"])
    st.markdown("**Run metadata**")
    st.json(meta)

# ------------------------------------------------------------------ export
with tabs[5]:
    for f in ("final/ranked_promos.json", "final/ranked_promos.csv", "final/editorial_report.md",
              "processed/rejected_candidates.json", "raw/promo_candidates.json"):
        p = out_root / f
        if p.exists():
            st.download_button(f"Download {p.name}", p.read_bytes(), file_name=p.name, use_container_width=True)
