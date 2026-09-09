"""
agreement.py — ensemble and confidence analysis across arms already collected.

No API calls. Every run so far extracted the same 391 images, so the results
already on disk support two analyses nobody paid for:

  ENSEMBLE      take the median of each field across arms. Independent errors
                cancel; a systematic one survives, which is diagnostic in
                itself — if the ensemble does not fix the kilojoule
                substitution, that confirms it is a shared bias rather than
                noise.

  CONFIDENCE    where the arms agree, is the answer more likely to be right?
                If so the deployment recommendation is not "use the best
                prompt" but "run several cheap ones, accept where they agree,
                route the rest to a human" — which is a triage rule, and
                usable in a way a leaderboard is not.

The second is the useful one. A benchmark says how good a model is; a
confidence signal says which of its outputs you can trust, which is what
anyone deploying it actually needs.

Usage
-----
    python3 agreement.py --arms baseline units strict cot
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

FIELDS = ["energy_kcal", "fat_g", "saturated_fat_g", "carbohydrates_g",
          "sugars_g", "protein_g", "salt_g"]


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", ".")
        for junk in ("g", "kcal", "kJ", "mg", "ml", "%", "<", "~", " "):
            s = s.replace(junk, "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.1, abs(b) * 0.02)


def agree(vals: list[float]) -> bool:
    """All values within tolerance of their own median."""
    if len(vals) < 2:
        return False
    med = statistics.median(vals)
    return all(close(v, med) for v in vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--out", type=Path, default=Path("agreement.md"))
    args = ap.parse_args()

    # code -> arm -> parsed dict, plus code -> truth
    got: dict[str, dict[str, dict]] = defaultdict(dict)
    truth: dict[str, dict] = {}
    loaded = []

    for arm in args.arms:
        p = args.results / f"{arm}.jsonl"
        if not p.exists():
            print(f"  skipping {arm} — not found")
            continue
        loaded.append(arm)
        for line in p.open(encoding="utf-8"):
            r = json.loads(line)
            truth[r["code"]] = r["truth"]
            if r.get("parsed"):
                got[r["code"]][arm] = r["parsed"]

    if len(loaded) < 2:
        print("need at least two arms")
        return 1

    single = {a: [0, 0] for a in loaded}      # hits, seen
    ens = [0, 0]
    # accuracy bucketed by how many arms agreed
    by_agree: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    coverage = [0, 0]                         # fields where arms agreed, total

    for code, per_arm in got.items():
        if len(per_arm) < 2:
            continue
        t = truth[code]
        for f in FIELDS:
            want = num(t.get(f))
            if want is None:
                continue

            vals = []
            for a in loaded:
                v = num((per_arm.get(a) or {}).get(f))
                if v is not None:
                    vals.append(v)
                    single[a][1] += 1
                    if close(v, want):
                        single[a][0] += 1
            if not vals:
                continue

            med = statistics.median(vals)
            ens[1] += 1
            if close(med, want):
                ens[0] += 1

            unanimous = agree(vals) and len(vals) >= 2
            key = "all arms agree" if unanimous else "arms disagree"
            by_agree[key][1] += 1
            if close(med, want):
                by_agree[key][0] += 1

            coverage[1] += 1
            if unanimous:
                coverage[0] += 1

    def pct(a, b):
        return f"{100*a/b:.1f}%" if b else "—"

    L = []
    add = L.append
    add("# Ensemble and confidence\n")
    add(f"Arms combined: {', '.join(loaded)}. No additional API calls — these "
        "analyses reuse extractions already collected.\n")

    add("## Ensemble vs single arms\n")
    add("Median of each field across arms.\n")
    add("| Source | Fields correct |")
    add("|---|---:|")
    for a in loaded:
        add(f"| {a} | {pct(*single[a])} |")
    add(f"| **median ensemble** | **{pct(*ens)}** |")
    add("")

    best = max(single.values(), key=lambda x: x[0] / x[1] if x[1] else 0)
    best_rate = best[0] / best[1] if best[1] else 0
    ens_rate = ens[0] / ens[1] if ens[1] else 0
    delta = (ens_rate - best_rate) * 100
    add(f"Ensemble is {delta:+.1f} points against the best single arm. "
        "A gain means the arms fail independently; no gain means they share "
        "a bias, which is the more interesting outcome — it says the error is "
        "in the model's reading of the panel, not in how it was asked.\n")

    add("## Agreement as a confidence signal\n")
    add("| Case | Fields | Accuracy |")
    add("|---|---:|---:|")
    for k in ("all arms agree", "arms disagree"):
        h, n = by_agree[k]
        add(f"| {k} | {n} | {pct(h, n)} |")
    add("")
    add(f"Arms agree on **{pct(*coverage)}** of fields.\n")

    hi = by_agree["all arms agree"]
    lo = by_agree["arms disagree"]
    if hi[1] and lo[1]:
        gap = (hi[0] / hi[1] - lo[0] / lo[1]) * 100
        add(f"Accuracy is **{gap:.0f} points higher** where the arms agree. ")
        add("If that gap holds, the deployment rule is not 'pick the best "
            "prompt' but 'run several cheap variants, accept the agreements, "
            "and route the disagreements to a reviewer' — turning an "
            "unreliable extractor into a triage system with a known "
            "human-review rate.\n")

    report = "\n".join(L)
    args.out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
