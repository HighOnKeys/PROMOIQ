"""Independent audit of recommended promos against the raw screenplay files.

It re-reads the episode .txt files with its own small regex parser (deliberately NOT PROMOIQ's parser) and checks
every shortlisted promo and held-back alternative:
  - every segment start/end is a real timestamp in the source (a block start/end or a scene boundary)
  - no cut falls inside a line of dialogue or a sound cue
  - total duration is 20-60 s and matches the reported duration; segments move forward in story time
  - every evidence quote appears in the cited source block
It also reports how many lines/events fall in the first 3 s (for a manual hook check).

    python -m evaluation.verify_against_source --input data/input outputs/final/ranked_promos.json
    python -m evaluation.verify_against_source --input data/input outputs/final/ranked_promos.json \
        --scripts outputs/promo_scripts.md     # also write each promo's source text (CONFIDENTIAL: keep it private)

Exit code 1 if any problem is found."""
import json, re, sys
from pathlib import Path

TS = re.compile(r"^Start: \((\d+):(\d+(?:\.\d+)?)\) \| End: \((\d+):(\d+(?:\.\d+)?)\)\s*$")
HDR = re.compile(r"^\*\*(.+?):\*\*\s*$")
SC = re.compile(r"^Scene (start|end): (\d+):(\d+(?:\.\d+)?)\s*$")

def load(input_dir):
    eps = {}
    for f in sorted(Path(input_dir).rglob("*.txt")):
        lines = f.read_text(encoding="utf-8").splitlines()
        m = re.search(r"EPISODE NUMBER:\s*Episode\s*(\d+)", lines[0] if lines else "")
        ep = int(m.group(1))
        blocks, bounds, hdr = [], set(), None
        for i, ln in enumerate(lines):
            h = HDR.match(ln.strip())
            if h:
                hdr = h.group(1); continue
            s = SC.match(ln.strip())
            if s:
                bounds.add(round(int(s.group(2)) * 60 + float(s.group(3)), 2)); continue
            t = TS.match(ln.strip())
            if t and hdr:
                a = int(t.group(1)) * 60 + float(t.group(2)); b = int(t.group(3)) * 60 + float(t.group(4))
                text = lines[i + 1].strip() if i + 1 < len(lines) else ""
                kind = "camera" if hdr.startswith("CAMERA") else "action" if hdr.startswith("ACTION") else \
                       "sound" if hdr.startswith("SOUND") else "dialogue"
                blocks.append(dict(kind=kind, hdr=hdr, start=round(a, 2), end=round(b, 2), text=text, line=i + 2))
                hdr = None
        eps[ep] = dict(file=f.name, blocks=blocks, bounds=bounds, lines=lines)
    return eps

def mmss(s):
    return f"{int(s // 60):02d}:{s - 60 * int(s // 60):05.2f}"

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Check ranked promos against the raw screenplays.")
    ap.add_argument("--input", required=True, help="folder holding the episode .txt files")
    ap.add_argument("ranked", nargs="+", help="ranked_promos.json file(s)")
    ap.add_argument("--scripts", default=None, help="optional: write promo source text here (private)")
    a = ap.parse_args()
    input_dir, ranked, out = a.input, a.ranked, a.scripts
    eps = load(input_dir)
    n_problems = 0
    print(f"episode files: {len(eps)}; numbers {min(eps)}..{max(eps)}; missing {sorted(set(range(1, 51)) - set(eps))}")
    print(f"timestamped blocks (independent count): {sum(len(e['blocks']) for e in eps.values())}")
    script = []
    for rp in ranked:
        R = json.load(open(rp))
        items = [("SHORTLIST", p) for p in R["promos"]] + [("ALT", p) for p in R.get("strong_alternatives_held_back", [])]
        print(f"\n=== {rp}: {len(R['promos'])} promos, {len(R.get('strong_alternatives_held_back', []))} alternatives")
        for tag, p in items:
            probs, segs = [], [dict(q) for q in p["segments"]]
            for q in segs:
                for side in ("start", "end"):
                    if side + "_s" not in q:
                        m_, s_ = q[side].split(":"); q[side + "_s"] = round(int(m_) * 60 + float(s_), 2)
            total = 0.0
            for k, s in enumerate(segs):
                e = eps[s["episode"]]
                stamps = {x["start"] for x in e["blocks"]} | {x["end"] for x in e["blocks"]} | e["bounds"]
                for side in ("start_s", "end_s"):
                    if not any(abs(s[side] - v) < 0.006 for v in stamps):
                        probs.append(f"seg{k+1} {side}={s[side]} not a source timestamp")
                if "start" in s and s["start"] != mmss(s["start_s"]): probs.append(f"seg{k+1} start string {s['start']} != {mmss(s['start_s'])}")
                if "end" in s and s["end"] != mmss(s["end_s"]): probs.append(f"seg{k+1} end string {s['end']} != {mmss(s['end_s'])}")
                for cut in (s["start_s"], s["end_s"]):
                    for x in e["blocks"]:
                        if x["kind"] in ("dialogue", "sound") and x["start"] + 0.005 < cut < x["end"] - 0.005:
                            probs.append(f"seg{k+1} cut {cut} splits {x['kind']} line {x['line']} ({x['start']}-{x['end']})")
                if s["end_s"] <= s["start_s"]: probs.append(f"seg{k+1} non-positive length")
                total += s["end_s"] - s["start_s"]
                if k and (s["episode"], s["start_s"]) < (segs[k-1]["episode"], segs[k-1]["end_s"]):
                    probs.append(f"seg{k+1} goes backwards in story time")
            if not 20 - 1e-6 <= total <= 60 + 1e-6: probs.append(f"duration {total:.2f}s outside 20-60")
            if "duration_s" in p and abs(p["duration_s"] - total) > 0.06: probs.append(f"duration_s {p['duration_s']} != {total:.2f}")
            if "total_duration_s" in p and abs(p["total_duration_s"] - total) > 0.06: probs.append(f"total_duration_s {p['total_duration_s']} != {total:.2f}")
            for ev in p.get("evidence", []):
                src_file, _, ln = ev.get("source", "::").rpartition(":")
                e = eps[ev["episode"]]
                try:
                    srcline = " ".join(e["lines"][int(ln) - 1:int(ln) + 3])
                except Exception:
                    srcline = ""
                if ev.get("text") and ev["text"].strip() not in srcline:
                    probs.append(f"evidence {ev.get('event_id')} text not on cited line {ln}")
            # what happens in the first 3 s of the promo (segment 1)
            s0 = segs[0]; e0 = eps[s0["episode"]]
            first3 = [x for x in e0["blocks"] if x["kind"] != "camera" and x["start"] < s0["start_s"] + 3.0 and x["end"] > s0["start_s"]]
            dlg3 = [x for x in first3 if x["kind"] == "dialogue" and x["start"] < s0["start_s"] + 3.0]
            segstr = " + ".join("E%d %s-%s" % (q["episode"], q["start_s"], q["end_s"]) for q in segs)
            print(f"{tag:9s} {p.get('promo_id', p.get('candidate_id'))} {segstr} "
                  f"dur={total:.1f}s first3s: {len(dlg3)} line(s), {len(first3)} non-camera events | "
                  + ("OK" if not probs else "PROBLEMS: " + "; ".join(probs)))
            n_problems += bool(probs)
            # private promo script for the qualitative read (no PROMOIQ annotations, source text only)
            if tag == "SHORTLIST":
                script.append(f"## {Path(rp).parent.parent.name}/{p['promo_id']}  ({total:.1f}s)")
                off = 0.0
                for s in segs:
                    e = eps[s["episode"]]
                    for x in e["blocks"]:
                        if x["kind"] == "camera": continue
                        if x["end"] > s["start_s"] + 0.005 and x["start"] < s["end_s"] - 0.005:
                            t = max(0.0, x["start"] - s["start_s"]) + off
                            who = x["hdr"] if x["kind"] == "dialogue" else x["kind"].upper()
                            script.append(f"  {t:5.1f}s  {who}: {x['text']}")
                    off += s["end_s"] - s["start_s"]
                    script.append("  -- cut --")
                script.append("")
    if out:
        Path(out).write_text("\n".join(script))
    print(f"\n{n_problems} promo(s) with problems")
    return 1 if n_problems else 0


if __name__ == "__main__":
    sys.exit(main())
