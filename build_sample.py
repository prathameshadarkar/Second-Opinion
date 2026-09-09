"""
build_sample.py — stream the Open Food Facts export down to a workable sample.

v2. The first version filtered on `image_nutrition_url`, which the API computes
and the bulk export does not carry — it matched nothing across a million
products. Image URLs have to be constructed from the product code and the
image revision found in the `images` object.

The full export is ~12 GB gzipped, around 4 million products. This never
decompresses it and never holds more than one product in memory. It writes:

    candidates.jsonl   every product that passes the filter
    sample.jsonl       the stratified N you will actually send to the model

Filter:

  · a nutrition image exists in `images`   — only ~4% of products have one,
                                             which is the binding constraint
  · nutrition_data_per == "100g"           — products recorded per serving show
                                             per-serving values on the panel,
                                             so the panel and the stored truth
                                             would disagree by construction
  · all seven target nutrients present     — no partial ground truth
  · energy > 0                             — a zero-calorie product hands the
                                             model seven free zeros

What this filter cannot do is tell whether the image is actually a nutrition
panel. That slot often holds a front-of-pack photo, and measuring how often is
the first result of the project — see make_labeller.py.

Usage
-----
    python3 build_sample.py --input openfoodfacts-products.jsonl.gz
    python3 build_sample.py --input ... --n 400 --debug
"""
from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

NUTRIENTS = {
    "energy_kcal": "energy-kcal_100g",
    "fat_g": "fat_100g",
    "saturated_fat_g": "saturated-fat_100g",
    "carbohydrates_g": "carbohydrates_100g",
    "sugars_g": "sugars_100g",
    "protein_g": "proteins_100g",
    "salt_g": "salt_100g",
}

KEEP = ["code", "product_name", "lang", "countries_tags",
        "nutrition_data_per", "serving_size"]

IMG_BASE = "https://images.openfoodfacts.org/images/products"


def code_path(code: str) -> str:
    """Open Food Facts splits long barcodes into directories.

    3017620422003 -> 301/762/042/2003
    Codes of eight digits or fewer are used whole.
    """
    code = str(code).strip()
    if len(code) <= 8:
        return code
    c = code.zfill(13) if len(code) < 13 else code
    return f"{c[:3]}/{c[3:6]}/{c[6:9]}/{c[9:]}"


def nutrition_image(p: dict) -> tuple[str, str] | None:
    """Find a nutrition image and return (url_400, language_suffix).

    Two schemas coexist in the dump:
      old:  images["nutrition_en"] = {"rev": "822", "sizes": {...}}
      new:  images["selected"]["nutrition"]["en"] = {"rev": ..., ...}

    The URL is {base}/{code_path}/{key}.{rev}.400.jpg
    """
    imgs = p.get("images")
    if not isinstance(imgs, dict):
        return None
    code = str(p.get("code") or "").strip()
    if not code:
        return None
    path = code_path(code)

    def build(key: str, entry: dict) -> tuple[str, str] | None:
        if not isinstance(entry, dict):
            return None
        rev = entry.get("rev") or entry.get("imgid")
        if rev is None:
            return None
        return f"{IMG_BASE}/{path}/{key}.{rev}.400.jpg", key

    # new schema first — it is the curated one
    sel = imgs.get("selected")
    if isinstance(sel, dict):
        nut = sel.get("nutrition")
        if isinstance(nut, dict):
            for lang in ("en", *sorted(nut.keys())):
                entry = nut.get(lang)
                if isinstance(entry, dict):
                    got = build(f"nutrition_{lang}", entry)
                    if got:
                        return got

    # old schema: flat keys like nutrition_en, nutrition_fr, nutrition
    flat = [k for k in imgs if isinstance(k, str) and k.startswith("nutrition")]
    for key in sorted(flat, key=lambda k: (k != "nutrition_en", k)):
        got = build(key, imgs[key])
        if got:
            return got
    return None


def slim(p: dict, img_url: str, img_key: str) -> dict:
    n = p["nutriments"]
    out = {k: p.get(k) for k in KEEP}
    out["image_nutrition_url"] = img_url
    out["image_key"] = img_key
    out["truth"] = {ours: float(n[theirs]) for ours, theirs in NUTRIENTS.items()}
    return out


def scan(path: Path, out: Path, debug: bool, limit: int = 0) -> tuple[Counter, Counter]:
    langs: Counter = Counter()
    reject: Counter = Counter()
    seen = kept = 0
    shown = 0

    with gzip.open(path, "rt", encoding="utf-8") as fh, out.open("w", encoding="utf-8") as w:
        for line in fh:
            if limit and seen >= limit:
                break
            seen += 1
            if seen % 250_000 == 0:
                print(f"  {seen:>9,} read · {kept:>7,} kept", file=sys.stderr)
            try:
                p = json.loads(line)
            except json.JSONDecodeError:
                reject["bad json"] += 1
                continue

            img = nutrition_image(p)
            if not img:
                reject["no nutrition image"] += 1
                continue
            if p.get("nutrition_data_per") != "100g":
                reject["not per 100g"] += 1
                continue
            n = p.get("nutriments") or {}
            if not all(n.get(k) is not None for k in NUTRIENTS.values()):
                reject["incomplete nutrients"] += 1
                continue
            try:
                if float(n["energy-kcal_100g"]) <= 0:
                    reject["zero energy"] += 1
                    continue
            except (TypeError, ValueError):
                reject["bad energy"] += 1
                continue

            url, key = img
            rec = slim(p, url, key)
            langs[rec.get("lang") or "??"] += 1
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            kept += 1

            if debug and shown < 3:
                print(f"\n  [debug] {rec['code']} — {rec.get('product_name')}", file=sys.stderr)
                print(f"          {url}", file=sys.stderr)
                shown += 1

    print(f"\n  {seen:,} read, {kept:,} kept", file=sys.stderr)
    print("\n  rejected:", file=sys.stderr)
    for r, c in reject.most_common():
        print(f"    {r:<24} {c:>9,}", file=sys.stderr)
    return langs, reject


def stratify(candidates: Path, n: int, seed: int, out: Path, langs: Counter) -> None:
    """Sample n spread across languages, not the head of the file."""
    rng = random.Random(seed)
    top = [l for l, _ in langs.most_common(6)]
    per = max(1, n // (len(top) + 1))
    buckets: dict[str, list] = {l: [] for l in top}
    buckets["other"] = []

    with candidates.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            rec = json.loads(line)
            b = rec.get("lang") if rec.get("lang") in buckets else "other"
            pool = buckets[b]
            if len(pool) < per:
                pool.append(rec)
            else:
                j = rng.randint(0, i)
                if j < per:
                    pool[j] = rec

    picked = [r for pool in buckets.values() for r in pool][:n]
    rng.shuffle(picked)
    with out.open("w", encoding="utf-8") as w:
        for r in picked:
            w.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nsample: {len(picked)} products", file=sys.stderr)
    for b, pool in buckets.items():
        if pool:
            print(f"  {b:<6} {len(pool)}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--candidates", type=Path, default=Path("candidates.jsonl"))
    ap.add_argument("--sample", type=Path, default=Path("sample.jsonl"))
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--debug", action="store_true", help="print the first few image URLs")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N products — use 50000 for a quick check first")
    ap.add_argument("--skip-scan", action="store_true")
    args = ap.parse_args()

    if args.skip_scan:
        langs = Counter()
        with args.candidates.open(encoding="utf-8") as fh:
            for line in fh:
                langs[json.loads(line).get("lang") or "??"] += 1
        print(f"reusing {args.candidates} ({sum(langs.values()):,})", file=sys.stderr)
    else:
        if not args.input.exists():
            sys.exit(f"not found: {args.input}")
        if args.limit:
            print(f"quick check: first {args.limit:,} products only", file=sys.stderr)
        print(f"streaming {args.input}", file=sys.stderr)
        langs, _ = scan(args.input, args.candidates, args.debug, args.limit)

    if not sum(langs.values()):
        print("\nnothing passed the filter — run inspect_export.py and send the output",
              file=sys.stderr)
        return 1

    print("\nlanguages:", file=sys.stderr)
    for l, c in langs.most_common(8):
        print(f"  {l:<6} {c:>7,}", file=sys.stderr)

    stratify(args.candidates, args.n, args.seed, args.sample, langs)
    print(f"\nwrote {args.sample}", file=sys.stderr)
    print("next: make_labeller.py", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
