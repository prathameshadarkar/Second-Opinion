"""
score.py — turn the raw extraction runs into the tables that are the deliverable.

Produces, per arm:

  · parse rate and field-level accuracy
  · the energy field on its own, because that is where the model failed in the
    smoke test and where the `units` arm was aimed
  · a specific count of the kJ-for-kcal substitution, detected by ratio rather
    than assumed — if a returned energy value is about 4.184x the truth, the
    model read the kilojoule figure off a dual-unit European panel
  · accuracy by language across the 27 in the sample
  · accuracy by panel style, since US panels are per-serving and require a
    conversion that European per-100g panels do not

It also writes disagreements.jsonl: the cases where model and database differ
most, for manual review. Open Food Facts is crowdsourced, so some of those are
database errors rather than model errors, and separating the two is the point.

Usage
-----
    python3 score.py
    python3 score.py --arms baseline strict --out report.md
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

FIELDS = ["energy_kcal", "fat_g", "saturated_fat_g", "carbohydrates_g",
          "sugars_g", "protein_g", "salt_g"]

KJ_PER_KCAL = 4.184
# A value within 3% of truth x 4.184 is the kilojoule figure, not a near miss.
KJ_TOL = 0.03


def num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", ".")
        for junk in ("g", "kcal", "kJ", "mg", "ml", "%", "<", "~", " "):
            s = s.replace(junk, "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def close(got: float, want: float) -> bool:
    """2% relative, with an absolute floor so 0.107g salt is not judged on 2%
    of a tiny number. Panels round; databases do not."""
    return abs(got - want) <= max(0.1, abs(want) * 0.02)


def is_kj_error(got: float, want: float) -> bool:
    if want <= 0:
        return False
    return abs(got / want - KJ_PER_KCAL) / KJ_PER_KCAL <= KJ_TOL


def per_serving(rec: dict) -> bool:
    """US and Canadian panels are per-serving by law; EU panels are per-100g.
    countries_tags is the available proxy."""
    tags = rec.get("countries_tags") or []
    return any(t in ("en:united-states", "en:canada") for t in tags)


def reparse(raw: str) -> dict | None:
    """Recover the answer object, preferring the LAST balanced object.

    Kept in step with run_extraction.py. Re-parsing the stored raw responses
    means an improvement to parsing applies to runs already completed, without
    spending another API call.
    """
    if not raw or "{" not in raw:
        return None
    for start in reversed([i for i, ch in enumerate(raw) if ch == "{"]):
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start:i + 1])
                    except (ValueError, json.JSONDecodeError):
                        break
                    if isinstance(obj, dict) and any(f in obj for f in FIELDS):
                        return obj
                    break
    try:
        return json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None


def load(path: Path, repar: bool = True) -> list[dict]:
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    if not repar:
        return rows
    recovered = 0
    for r in rows:
        if r.get("raw"):
            got = reparse(r["raw"])
            if got and not r.get("parsed"):
                recovered += 1
            if got:
                r["parsed"] = got
    if recovered:
        print(f"  {path.name}: recovered {recovered} previously-unparsed responses")
    return rows


def score_arm(rows: list[dict]) -> dict:
    n = len(rows)
    parsed = [r for r in rows if r.get("parsed")]
    field_hits: Counter = Counter()
    field_seen: Counter = Counter()
    field_err: dict[str, list[float]] = defaultdict(list)
    kj_errors = 0
    energy_seen = 0
    by_lang: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    by_style: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    disagreements = []

    for r in parsed:
        got_all, truth = r["parsed"], r["truth"]
        style = "per-serving (US/CA)" if per_serving(r) else "per-100g (EU)"
        lang = r.get("lang") or "??"
        worst = None

        for f in FIELDS:
            want = num(truth.get(f))
            got = num(got_all.get(f))
            if want is None:
                continue
            field_seen[f] += 1
            if got is None:
                continue
            ok = close(got, want)
            if ok:
                field_hits[f] += 1
            field_err[f].append(abs(got - want))

            if f == "energy_kcal":
                energy_seen += 1
                if not ok and is_kj_error(got, want):
                    kj_errors += 1

            by_lang[lang][1] += 1
            by_style[style][1] += 1
            if ok:
                by_lang[lang][0] += 1
                by_style[style][0] += 1
            elif want > 0:
                rel = abs(got - want) / abs(want)
                if worst is None or rel > worst[0]:
                    worst = (rel, f, got, want)

        if worst and worst[0] > 0.25:
            disagreements.append({
                "code": r["code"], "lang": lang, "style": style,
                "field": worst[1], "model": worst[2], "database": worst[3],
                "rel_error": round(worst[0], 2),
                "image": f"https://images.openfoodfacts.org/images/products/",
            })

    return {
        "n": n,
        "parsed": len(parsed),
        "parse_rate": len(parsed) / n if n else 0,
        "field_hits": field_hits,
        "field_seen": field_seen,
        "field_err": field_err,
        "kj_errors": kj_errors,
        "energy_seen": energy_seen,
        "by_lang": by_lang,
        "by_style": by_style,
        "disagreements": disagreements,
    }


def pct(a: int, b: int) -> str:
    return f"{100*a/b:.1f}%" if b else "—"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", type=Path, default=Path("results"))
    ap.add_argument("--arms", nargs="*", default=["baseline", "units", "strict"])
    ap.add_argument("--out", type=Path, default=Path("report.md"))
    ap.add_argument("--min-lang", type=int, default=10,
                    help="hide languages with fewer than this many scored fields")
    args = ap.parse_args()

    scored = {}
    for arm in args.arms:
        p = args.results / f"{arm}.jsonl"
        if not p.exists():
            print(f"skipping {arm} — {p} not found")
            continue
        scored[arm] = score_arm(load(p))
    if not scored:
        return 1

    L: list[str] = []
    add = L.append

    add("# Nutrition panel extraction — results\n")
    add(f"Model: `meta/llama-3.2-11b-vision-instruct` via NVIDIA NIM. "
        f"{list(scored.values())[0]['n']} hand-labelled panel images.\n")

    add("## Parse rate and overall accuracy\n")
    add("| Arm | Parsed | Parse rate | Fields correct |")
    add("|---|---:|---:|---:|")
    for arm, s in scored.items():
        hits, seen = sum(s["field_hits"].values()), sum(s["field_seen"].values())
        add(f"| {arm} | {s['parsed']}/{s['n']} | {pct(s['parsed'], s['n'])} | "
            f"{pct(hits, seen)} |")
    add("")

    add("## By field\n")
    add("| Field | " + " | ".join(f"{a} acc" for a in scored) + " | median abs error |")
    add("|---|" + "---:|" * (len(scored) + 1))
    for f in FIELDS:
        cells = []
        for arm, s in scored.items():
            cells.append(pct(s["field_hits"][f], s["field_seen"][f]))
        errs = scored[list(scored)[-1]]["field_err"].get(f) or [0]
        add(f"| `{f}` | " + " | ".join(cells) + f" | {statistics.median(errs):.2f} |")
    add("")

    add("## The kilojoule substitution\n")
    add("A dual-unit European panel shows energy twice — `180kJ / 42kcal`. "
        "Returning 180 is not a misread of a digit; it is reading the wrong "
        "column, and it recurs on every such panel. Detected here by ratio: a "
        "value within 3% of 4.184x the truth is the kJ figure.\n")
    add("| Arm | Energy correct | kJ-for-kcal errors |")
    add("|---|---:|---:|")
    for arm, s in scored.items():
        add(f"| {arm} | {pct(s['field_hits']['energy_kcal'], s['field_seen']['energy_kcal'])} "
            f"| {s['kj_errors']} ({pct(s['kj_errors'], s['energy_seen'])}) |")
    add("")

    add("## By panel style\n")
    add("US and Canadian panels are per-serving by law; European panels are "
        "per-100g. The former require reading a serving size and converting.\n")
    add("| Style | " + " | ".join(scored) + " |")
    add("|---|" + "---:|" * len(scored))
    styles = sorted({s for sc in scored.values() for s in sc["by_style"]})
    for st in styles:
        cells = [pct(*sc["by_style"].get(st, [0, 0])) for sc in scored.values()]
        n_fields = list(scored.values())[0]["by_style"].get(st, [0, 0])[1]
        add(f"| {st} (n={n_fields}) | " + " | ".join(cells) + " |")
    add("")

    add("## By language\n")
    best = scored.get("strict") or list(scored.values())[-1]
    add("| Language | Fields scored | " + " | ".join(scored) + " |")
    add("|---|---:|" + "---:|" * len(scored))
    langs = sorted(best["by_lang"], key=lambda l: -best["by_lang"][l][1])
    for lg in langs:
        seen = best["by_lang"][lg][1]
        if seen < args.min_lang:
            continue
        cells = [pct(*sc["by_lang"].get(lg, [0, 0])) for sc in scored.values()]
        add(f"| {lg} | {seen} | " + " | ".join(cells) + " |")
    add("")

    add("## Disagreements to review\n")
    d = best["disagreements"]
    add(f"{len(d)} cases where the model differs from the database by more than "
        "25% on at least one field. Open Food Facts is crowdsourced and "
        "self-reported, so an unknown share of these are database errors rather "
        "than model errors. Reviewing a sample of them by eye is what makes the "
        "corrected accuracy figure meaningful.\n")
    add("Written to `disagreements.jsonl`.\n")

    report = "\n".join(L)
    args.out.write_text(report, encoding="utf-8")
    Path("disagreements.jsonl").write_text(
        "\n".join(json.dumps(x) for x in d), encoding="utf-8")

    print(report)
    print(f"\nwrote {args.out} and disagreements.jsonl ({len(d)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
