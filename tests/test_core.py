"""Unit tests: timestamps, parsing, beats, duration validation, sequence construction, ranking.
All fixtures are synthetic (no Kuku content in the test suite)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from config import get_settings
from src.llm import extract_json
from src.parser import build_beats, canonicalise, parse_episode, parse_range, parse_timestamp, TimestampError
from src.parser.timestamps import format_timestamp

SCRIPT = textwrap.dedent('''\
    EPISODE NUMBER: Episode 1
    EPISODE LOGLINE: Mara is betrayed by her sister Vera at the harbour.
    CONTINUITY NOTES: Test fixture.

    --- SCRIPT START ---

    SCENE 01
    Scene heading: EXT. HARBOUR – NIGHT
    Scene start: 00:00.00
    Scene end: 00:40.00

    Actions and dialogue:

    **CAMERA:**
    Start: (00:00.00) | End: (00:04.00)
    Extreme close-up on Mara's shocked face.

    **SOUND FX:**
    Start: (00:00.20) | End: (00:01.00)
    A sharp slap echoes.

    **LADY MARA (Furious, Screaming):**
    Start: (00:01.00) | End: (00:05.50)
    "How dare you betray me, sister!"

    **CAMERA:**
    Start: (00:04.00) | End: (00:10.00)
    Medium shot of Vera smiling coldly.

    **VERA (Mocking):**
    Start: (00:06.00) | End: (00:09.00)
    "You were always the fool."

    **CAMERA:**
    Start: (00:10.00) | End: (00:22.00)
    Close-up on Mara trembling.

    **MARA (Desperate):**
    Start: (00:10.50) | End: (00:13.00)
    "Then who poisoned our father?"

    **ACTION:**
    Start: (00:13.00) | End: (00:21.00)
    Vera steps toward her and grabs her wrist.

    **CAMERA:**
    Start: (00:22.00) | End: (00:40.00)
    Wide shot of the dark harbour.

    **VERA (Threatening):**
    Start: (00:23.00) | End: (00:26.00)
    "Ask him yourself... if you live long enough."
    ''')


@pytest.fixture()
def episode(tmp_path: Path):
    p = tmp_path / "episode-001-Episode 9.txt"
    p.write_text(SCRIPT)
    ep, events, issues = parse_episode(p)
    canonicalise(events)
    return ep, events, issues


# ------------------------------------------------------------------ timestamps
@pytest.mark.parametrize("raw,expected", [
    ("00:13.50", 13.5), ("01:25.75", 85.75), ("1:02:03.5", 3723.5), ("00:00.00", 0.0),
    ("02:18.00", 138.0), (" (00:04.80) ", 4.8), ("13.5", 13.5), ("00:05", 5.0), ("00:59.99", 59.99),
])
def test_parse_timestamp(raw, expected):
    assert parse_timestamp(raw) == pytest.approx(expected)


def test_decimal_is_seconds_not_minutes():
    # 01:25.75 must be 85.75 s, never 1.2575 minutes or 85.45
    assert parse_timestamp("01:25.75") == 85.75


@pytest.mark.parametrize("bad", ["", "abc", "00:61.00", "1:2:3:4", None])
def test_parse_timestamp_rejects(bad):
    with pytest.raises(TimestampError):
        parse_timestamp(bad)


def test_format_roundtrip():
    for s in (0.0, 4.8, 85.75, 138.0, 3599.99):
        assert parse_timestamp(format_timestamp(s)) == pytest.approx(s)


def test_parse_range_tolerant():
    assert parse_range("Start: (00:01.00) | End: (00:04.80)") == ("00:01.00", "00:04.80")
    assert parse_range("start: 00:01.00 | end: 00:04.80") == ("00:01.00", "00:04.80")
    assert parse_range("no timestamps here") is None


# --------------------------------------------------------------------- parsing
def test_parse_episode_structure(episode):
    ep, events, issues = episode
    assert ep.episode == 1 and ep.label == "Episode 1"
    assert "betrayed" in ep.logline
    assert len(ep.scenes) == 1 and ep.scenes[0].end == 40.0
    types = [e.event_type for e in events]
    assert types.count("camera") == 4 and types.count("dialogue") == 4
    assert "sound" in types and "action" in types
    assert issues == []


def test_speaker_cues_and_alias_folding(episode):
    _, events, _ = episode
    first = next(e for e in events if e.event_type == "dialogue")
    assert first.delivery == ["Furious", "Screaming"]
    assert first.speaker == "MARA"                      # LADY MARA folded because MARA also speaks
    assert first.text == "How dare you betray me, sister!"
    assert all(e.source.line > 0 for e in events)


def test_missing_timestamp_is_flagged_not_invented(tmp_path):
    p = tmp_path / "episode-002.txt"
    p.write_text("--- SCRIPT START ---\nSCENE 01\nScene start: 00:00.00\nScene end: 00:10.00\n"
                 "**CAMERA:**\nStart: (00:00.00) | End: (00:05.00)\nShot one.\n\n**ACTION:**\nNo timestamp here.\n")
    _, events, issues = parse_episode(p)
    bad = [e for e in events if e.timing_status != "exact"]
    assert len(bad) == 1 and bad[0].confidence < 1.0
    assert any("missing" in i["issue"] for i in issues)


# ----------------------------------------------------------------------- beats
def test_beats_never_cut_through_dialogue(episode):
    ep, events, _ = episode
    beats = build_beats(ep, events)
    bounds = {b.start for b in beats} | {b.end for b in beats}
    for e in events:
        if e.event_type == "dialogue":
            assert not any(e.start + 0.05 < t < e.end - 0.05 for t in bounds), e.text
    # the 00:04.00 camera cut falls inside Mara's 01.00-05.50 line, so it must be merged away
    assert 4.0 not in {b.start for b in beats}


def test_beat_boundaries_are_source_timestamps(episode):
    ep, events, _ = episode
    src = {e.start for e in events} | {e.end for e in events} | {0.0, 40.0}
    for b in build_beats(ep, events):
        assert b.start in src and b.end in src
        assert b.start_raw and b.end_raw


# ---------------------------------------------------------- pipeline fixtures
@pytest.fixture()
def tiny_show(tmp_path: Path):
    """Three synthetic episodes so search/validation/ranking can run end to end."""
    d = tmp_path / "Test Show-scripts"
    d.mkdir()
    for n in (1, 2, 3):
        (d / f"episode-00{n}-Episode {n}.txt").write_text(SCRIPT.replace("Episode 1", f"Episode {n}"))
    from src.pipeline import run

    st = get_settings()
    st.model.provider = "heuristic"
    st.model.judge_provider = ""
    st.model.cache_dir = tmp_path / "cache"
    res = run(d, settings=st, out_root=tmp_path / "out")
    return res, tmp_path / "out"


def test_end_to_end_outputs(tiny_show):
    res, out = tiny_show
    for f in ("final/ranked_promos.json", "final/ranked_promos.csv", "final/editorial_report.md",
              "raw/parsed_events.json", "raw/promo_candidates.json", "processed/story_bible.json",
              "processed/narrative_graph.json", "processed/rejected_candidates.json"):
        assert (out / f).exists(), f
    assert res["meta"]["episodes"] == 3


def test_every_candidate_respects_duration_and_order(tiny_show):
    res, _ = tiny_show
    st = get_settings()
    assert res["candidates"], "search produced nothing"
    for c in res["candidates"]:
        total = sum(s.end - s.start for s in c.segments)
        assert st.constraints.min_duration - 1e-6 <= total <= st.constraints.max_duration + 1e-6
        assert abs(total - c.duration) < 0.01
        for a, b in zip(c.segments, c.segments[1:]):
            assert (a.episode, a.end) <= (b.episode, b.start)


def test_validator_rejects_bad_duration(tiny_show):
    from src.evaluation.validator import HardValidator

    res, _ = tiny_show
    c = res["candidates"][0].model_copy(deep=True)
    c.segments[0].end = c.segments[0].start + 5.0
    c.duration = 5.0
    v = HardValidator(res["show"], get_settings().constraints).validate(c)
    assert not v["valid"] and "duration_20_60" in v["failed"]


def test_validator_rejects_invented_timestamp(tiny_show):
    from src.evaluation.validator import HardValidator

    res, _ = tiny_show
    c = res["candidates"][0].model_copy(deep=True)
    c.segments[0].start += 0.37        # not a real screenplay timestamp
    c.duration = round(sum(s.end - s.start for s in c.segments), 3)
    v = HardValidator(res["show"], get_settings().constraints).validate(c)
    assert "seg1_start_is_source_timestamp" in v["failed"]


def test_ranked_promos_are_valid_and_unique(tiny_show):
    res, out = tiny_show
    data = json.loads((out / "final/ranked_promos.json").read_text())
    ids = [p["candidate_id"] for p in data["promos"]]
    assert len(ids) == len(set(ids))
    for p in data["promos"]:
        assert p["hook"] and p["cliffhanger"] and p["why_it_works"]
        assert 20 <= p["total_duration_s"] <= 60
        assert p["evidence"]
    ranks = [p["final_score"] for p in data["promos"]]
    assert ranks == sorted(ranks, reverse=True)


# --------------------------------------------------------------------- ranking
def test_heuristic_cold_viewer_claims_only_what_is_on_screen(tiny_show):
    """Stakes must be a spoken line (never a camera direction); a named conflict must be a known antagonistic pair.
    Worst case forced: camera lines carry the highest stakes/conflict signals and the show has no known conflicts."""
    from src.evaluation.judges import ColdViewerJudge

    res, _ = tiny_show
    show = res["show"]
    camera = [e for e in show.events.values() if e.event_type == "camera"]
    assert camera
    for e in camera:
        sg = show.timelines[e.episode].signals[e.event_id]
        sg.S, sg.C, sg.E = 5.0, 5.0, 5.0
    camera_text = {e.text[:160] for e in camera}
    saved, show.conflicts = show.conflicts, {}
    try:
        judge = ColdViewerJudge(show, None)
        for c in res["candidates"][:40]:
            r = judge.judge(c)
            assert r.why_it_matters not in camera_text
            assert r.what_is_happening not in camera_text
            assert r.central_conflict == "not explicit on screen"
    finally:
        show.conflicts = saved


def test_judge_saying_no_open_question_rejects_candidate(tmp_path, monkeypatch):
    """A high generator cliffhanger score must not outweigh the integrity judge's verdict that nothing is left open."""
    from src.evaluation import judges
    from src.pipeline import run

    orig = judges.IntegrityJudge._heuristic

    def no_question(self, p):
        r = orig(self, p)
        r.ending_leaves_question = False
        return r

    monkeypatch.setattr(judges.IntegrityJudge, "_heuristic", no_question)
    d = tmp_path / "Test Show-scripts"
    d.mkdir()
    for n in (1, 2, 3):
        (d / f"episode-00{n}-Episode {n}.txt").write_text(SCRIPT.replace("Episode 1", f"Episode {n}"))
    st = get_settings()
    st.model.provider = "heuristic"
    st.model.judge_provider = ""
    st.model.cache_dir = tmp_path / "cache"
    res = run(d, settings=st, out_root=tmp_path / "out")
    assert not res["ranked"]
    rejected = json.loads((tmp_path / "out/processed/rejected_candidates.json").read_text())
    gate = st.gates.cliffhanger_pass
    # every candidate whose cliffhanger score passed is now rejected by the judge's verdict instead
    passed_score = [x for x in rejected if x["scores"]["cliffhanger"] >= gate]
    assert passed_score and all(any("no open question" in r for r in x["reasons"]) for x in passed_score)
    # and the verdict is not reported twice for candidates the score gate already rejected
    assert not any(any("no open question" in r for r in x["reasons"]) for x in rejected if x["scores"]["cliffhanger"] < gate)


def _run_without_sentence_transformers(tmp_path, monkeypatch):
    import sys
    from src.pipeline import run

    monkeypatch.setitem(sys.modules, "sentence_transformers", None)   # import now raises ImportError
    d = tmp_path / "Test Show-scripts"
    d.mkdir()
    for n in (1, 2, 3):
        # different words per episode, so later segments bring new vocabulary (that is what broke the old fallback)
        extra = {1: "", 2: " The harbour lanterns burn.", 3: " Cardinal ledgers vanish at midnight."}[n]
        (d / f"episode-00{n}-Episode {n}.txt").write_text(
            SCRIPT.replace("Episode 1", f"Episode {n}").replace("You were always the fool.", "You were always the fool." + extra))
    st = get_settings()
    st.model.provider = "heuristic"
    st.model.judge_provider = ""
    st.model.cache_dir = tmp_path / "cache"
    return run(d, settings=st, out_root=tmp_path / "out")


def test_tfidf_fallback_vectors_stay_comparable(tmp_path, monkeypatch):
    """Without sentence-transformers, segments primed in separate batches must still be comparable.
    The old fallback refitted TF-IDF per batch, so the vector length changed and np.dot crashed."""
    from src.construction.search import SegmentSimilarity

    res = _run_without_sentence_transformers(tmp_path, monkeypatch)
    show = res["show"]
    sim = SegmentSimilarity(show)
    segs = [(ep, k, k) for ep, tl in show.timelines.items() for k in range(len(tl.beats))]
    ep1 = [x for x in segs if x[0] == 1]
    rest = [x for x in segs if x[0] != 1]
    sim.prime(ep1)                      # first batch: episode 1 only
    first = sim.promo_vec(ep1[:2])
    sim.prime(rest)                     # later batch brings words episode 1 never used
    later = sim.promo_vec(rest[-2:])
    assert sim.backend == "tfidf"
    assert first.shape == later.shape
    assert -1e-6 <= float(first @ later) <= 1 + 1e-6


def test_pipeline_runs_end_to_end_on_tfidf_fallback(tmp_path, monkeypatch):
    res = _run_without_sentence_transformers(tmp_path, monkeypatch)
    meta = json.loads((tmp_path / "out/final/run_metadata.json").read_text())
    assert meta["models"]["embeddings"] == "tfidf"
    assert res["ranked"]


def test_mmr_prefers_diverse_item():
    from src.ranking.portfolio import mmr_shortlist
    from src.schemas import PromoCandidate, Segment

    def mk(pid, ep, a, b, theme="x"):
        return PromoCandidate(promo_id=pid, mode="continuous", duration=b - a, theme=theme,
                              segments=[Segment(order=1, episode=ep, start=a, end=b, start_raw="", end_raw="", beat_ids=[pid])])

    a, a2, b = mk("A", 1, 0, 30), mk("A2", 1, 2, 32), mk("B", 2, 0, 30)
    q = {"A": 9.0, "A2": 8.9, "B": 8.0}

    def sim(x, y):
        return 0.95 if {x.promo_id, y.promo_id} == {"A", "A2"} else 0.0

    sim.detail = lambda x, y: {"combined": sim(x, y)}
    chosen, dups = mmr_shortlist([a, a2, b], q, sim, k=2, lam=0.7, min_quality_ratio=0.5, dup_threshold=0.6)
    assert [c.promo_id for c in chosen] == ["A", "B"]
    assert any(d["candidate"] == "A2" for d in dups)


# ------------------------------------------------------------------- LLM layer
def test_extract_json_handles_fences_and_noise():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"beats": []} hope that helps') == {"beats": []}
    with pytest.raises(ValueError):
        extract_json("no json at all")


def test_gateway_waits_on_rate_limit_and_stops_on_daily_quota(tmp_path, monkeypatch):
    from src.llm.client import LLMClient, LLMError, ModelGateway
    import src.llm.client as client_mod

    slept = []
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: slept.append(s))

    class Flaky(LLMClient):
        provider = "fake_rl"
        calls = 0

        def _generate(self, system, prompt):
            Flaky.calls += 1
            if Flaky.calls == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: please retry in 7s")
            if "quota-test" in prompt:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: GenerateRequestsPerDayPerProjectPerModel")
            return '{"ok": true}'

    gw = ModelGateway(Flaky("m"), tmp_path / "c", tmp_path / "raw.jsonl")
    gw.min_interval = 0
    assert gw.generate("t", "s", "hello") == '{"ok": true}'
    assert any(7 < s < 9 for s in slept)            # honoured the server's retry hint
    with pytest.raises(LLMError, match="daily quota"):
        gw.generate("t", "s", "quota-test")
    n = Flaky.calls
    with pytest.raises(LLMError, match="exhausted"):
        gw.generate("t", "s", "another prompt")      # fails fast, no new API call
    assert Flaky.calls == n


def test_llm_annotator_flags_unverified_quotes_and_falls_back(episode, tmp_path):
    """Mock provider: one beat gets a fabricated quote, one beat is missing from the reply."""
    from src.dramatic.features import HeuristicAnnotator, LLMAnnotator, event_signal
    from src.llm.client import LLMClient, ModelGateway

    ep, events, _ = episode
    beats = build_beats(ep, events)
    ev = {e.event_id: e for e in events}
    sig = {e.event_id: event_signal(e) for e in events}

    class Fake(LLMClient):
        provider = "fake"

        def _generate(self, system, prompt):
            return json.dumps({"beats": [{"beat_id": beats[0].beat_id, "categories": ["betrayal", "nonsense"],
                                          "scores": {"surprise": 9}, "hook_signal": 5, "cliff_signal": 1,
                                          "evidence_quote": "a line that is not in the script"}]})

    gw = ModelGateway(Fake("m"), tmp_path / "c", tmp_path / "raw.jsonl")
    out = LLMAnnotator(gw, HeuristicAnnotator()).annotate_episode(ep, beats, ev, sig)
    assert out[0].scores["surprise"] == 5.0                  # clipped to rubric
    assert out[0].categories == ["betrayal"]                 # unknown label dropped
    assert "UNVERIFIED" in out[0].reasoning                  # hallucinated quote flagged
    assert all(o.annotator.startswith("heuristic") for o in out[1:])
    assert (tmp_path / "raw.jsonl").exists()                 # raw response logged unedited
