"""
inspect_export.py — find out what the bulk export actually contains.

build_sample.py filtered on fields that exist in the API response. The bulk
export is a different serialisation and may not carry the same convenience
fields, so this reads a few thousand products and reports which of the filter's
assumptions actually hold.

Run it, paste the output, and the filter gets corrected to match reality.

    python3 inspect_export.py --input openfoodfacts-products.jsonl.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path

NUTRIENT_KEYS = ["energy-kcal_100g", "fat_100g", "saturated-fat_100g",
                 "carbohydrates_100g", "sugars_100g", "proteins_100g", "salt_100g"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--n", type=int, default=3000, help="products to inspect")
    args = ap.parse_args()

    keys: Counter = Counter()
    has_img_url = has_images = has_nutriments = 0
    per_values: Counter = Counter()
    nutrient_present: Counter = Counter()
    image_key_shapes: Counter = Counter()
    example_with_nutrition_image = None
    example_any = None

    with gzip.open(args.input, "rt", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= args.n:
                break
            try:
                p = json.loads(line)
            except json.JSONDecodeError:
                continue

            if example_any is None:
                example_any = p
            keys.update(p.keys())

            if p.get("image_nutrition_url"):
                has_img_url += 1

            imgs = p.get("images")
            if imgs:
                has_images += 1
                if isinstance(imgs, dict):
                    image_key_shapes.update(k for k in imgs.keys())
                elif isinstance(imgs, list):
                    image_key_shapes["<list>"] += 1

            n = p.get("nutriments")
            if n:
                has_nutriments += 1
                for k in NUTRIENT_KEYS:
                    if n.get(k) is not None:
                        nutrient_present[k] += 1

            per_values[str(p.get("nutrition_data_per"))] += 1

            if example_with_nutrition_image is None and isinstance(imgs, dict):
                if any(str(k).startswith("nutrition") for k in imgs):
                    example_with_nutrition_image = p

    n = min(args.n, i + 1)
    print(f"inspected {n:,} products\n")

    print("=== the filter's assumptions ===")
    print(f"  image_nutrition_url present : {has_img_url:,}  ({100*has_img_url/n:.1f}%)")
    print(f"  images object present       : {has_images:,}  ({100*has_images/n:.1f}%)")
    print(f"  nutriments present          : {has_nutriments:,}  ({100*has_nutriments/n:.1f}%)")

    print("\n=== nutrition_data_per values ===")
    for v, c in per_values.most_common(6):
        print(f"  {v!r:<12} {c:,}")

    print("\n=== how often each nutrient is present ===")
    for k in NUTRIENT_KEYS:
        c = nutrient_present[k]
        print(f"  {k:<24} {c:>6,}  ({100*c/n:.1f}%)")

    print("\n=== image keys seen (top 15) ===")
    for k, c in image_key_shapes.most_common(15):
        print(f"  {k!r:<22} {c:,}")

    print("\n=== top-level fields (top 30) ===")
    print("  " + ", ".join(k for k, _ in keys.most_common(30)))

    ex = example_with_nutrition_image or example_any
    if ex:
        print(f"\n=== example product: {ex.get('code')} ===")
        imgs = ex.get("images")
        if isinstance(imgs, dict):
            print("  images keys:", list(imgs.keys())[:12])
            for k in imgs:
                if str(k).startswith("nutrition"):
                    print(f"  images[{k!r}] = {json.dumps(imgs[k])[:300]}")
                    break
        print("  image_nutrition_url:", ex.get("image_nutrition_url"))
        print("  nutrition_data_per :", ex.get("nutrition_data_per"))
        nut = ex.get("nutriments") or {}
        print("  nutriment keys (first 12):", list(nut.keys())[:12])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
