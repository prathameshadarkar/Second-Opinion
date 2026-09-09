"""
prepare_eval_app.py — build the small bundle the Streamlit explorer loads.

The raw results carry every model response in full; that is what you want on
disk and not what you want in a deployed app. This reduces them to one record
per image — the truth, what each arm returned, and whether the arms agreed —
which comes to a few hundred KB and commits cleanly.

No API key is involved anywhere. The app reads this file and nothing else, so
it can be public without exposing credentials or spending quota.

Usage
-----
    python3 prepare_eval_app.py --arms baseline units strict cot units-full
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

FIELDS = ["energy_kcal", "fat_g", "saturated_fat_g", "carbohydrates_g",
          "sugars_g", "protein_g", "salt_g"]
KJ = 4.184


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", ".")
        for j in ("g", "kcal", "kJ", "mg", "ml", "%", "<", "~", " "):
            s = s.replace(j, "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def close(a, b):
    return abs(a - b) <= max(0.1, abs(b) * 0.02)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--sample", type=Path, default=Path("sample.jsonl"))
    ap.add_argument("--arms", nargs="+", required=True)
    ap.add_argument("--out", type=Path, default=Path("app_data/eval.json"))
    a = ap.parse_args()

    images = {}
    if a.sample.exists():
        for line in a.sample.open(encoding="utf-8"):
            r = json.loads(line)
            images[r["code"]] = r.get("image_nutrition_url")

    recs: dict[str, dict] = {}
    loaded = []
    for arm in a.arms:
        p = a.results / f"{arm}.jsonl"
        if not p.exists():
            print(f"  skipping {arm} — not found")
            continue
        loaded.append(arm)
        for line in p.open(encoding="utf-8"):
            r = json.loads(line)
            code = r["code"]
            rec = recs.setdefault(code, {
                "code": code,
                "lang": r.get("lang"),
                "name": None,
                "image": images.get(code),
                "style": "per-serving" if any(
                    t in ("en:united-states", "en:canada")
                    for t in (r.get("countries_tags") or [])) else "per-100g",
                "truth": {f: num(r["truth"].get(f)) for f in FIELDS},
                "arms": {},
            })
            if r.get("parsed"):
                rec["arms"][arm] = {f: num(r["parsed"].get(f)) for f in FIELDS}

    # per-record agreement and correctness, precomputed so the app stays simple
    for rec in recs.values():
        agree, correct = {}, {}
        for f in FIELDS:
            vals = [v[f] for v in rec["arms"].values() if v.get(f) is not None]
            want = rec["truth"].get(f)
            if len(vals) >= 2:
                med = statistics.median(vals)
                agree[f] = all(close(v, med) for v in vals)
            else:
                agree[f] = None
            if want is not None and vals:
                med = statistics.median(vals)
                correct[f] = close(med, want)
                # flag the kilojoule substitution explicitly
                if not correct[f] and want > 0 and any(
                        abs(v / want - KJ) / KJ <= 0.03 for v in vals if v):
                    correct[f] = "kj"
            else:
                correct[f] = None
        rec["agree"] = agree
        rec["consensus"] = correct

    out = {
        "arms": loaded,
        "fields": FIELDS,
        "records": sorted(recs.values(), key=lambda r: r["code"]),
    }
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out), encoding="utf-8")
    kb = a.out.stat().st_size / 1024
    print(f"wrote {a.out} — {len(out['records'])} records, {len(loaded)} arms, {kb:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
