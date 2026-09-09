# Method

## Sampling

The September 2026 export is ~12 GB gzipped, around 4.7M products. It is
streamed line by line and never decompressed.

Filter, and why each part is there:

| Criterion | Reason |
|---|---|
| A nutrition image exists in `images` | Required |
| `nutrition_data_per == "100g"` | Products recorded per serving show per-serving values on the panel, so the stored truth and the printed label disagree by construction |
| All seven target nutrients present | No partial ground truth |
| Energy > 0 | A zero-calorie product hands the model seven free zeros and tests nothing |

242,597 candidates survive. 399 are drawn **stratified by language** — equal
numbers per bucket rather than proportional.

That is deliberate and it has a cost. English has 192,661 candidates and Czech
2,411; equal sampling gives equal power to test language effects and makes the
sample unrepresentative of the corpus. **Any corpus-level rate would have to be
reweighted**, and none is quoted.

## Image URLs

Not present in the bulk export. Constructed as:

```
https://images.openfoodfacts.org/images/products/{path}/{key}.{rev}.400.jpg
```

where `path` splits barcodes longer than eight digits into `3/3/3/rest`
(`3017620422003` → `301/762/042/2003`) and `key` is `nutrition_<lang>`.

## Labelling

Every sampled image classified by eye before any API call:

| Label | Meaning |
|---|---|
| `panel` | A readable nutrition table — all seven values legible |
| `partial` | A panel, but cut off, blurred or angled enough to lose values |
| `not-panel` | Front of pack, ingredients list, or something else |
| `unreadable` | Too dark, too small, or a dead image URL |

`partial` earns a separate label because those are cases where model and
database disagree and **neither is wrong**. Folding them into `panel` makes the
extraction score look worse than the model deserves; dropping them hides a
real-world condition.

Result: 97.0% panel, 1.0% partial, 1.3% not-panel, 0.5% unreadable.

## Extraction

`meta/llama-3.2-11b-vision-instruct` via NVIDIA NIM, temperature 0,
`max_tokens=1400`, images base64-encoded at 400px unless noted.

Four prompt variants:

| Arm | |
|---|---|
| `baseline` | Field list, per-100g requirement, null for illegible values |
| `units` | Adds an explicit dual-unit warning: return kcal, not kJ, with the "4x too large" diagnostic |
| `strict` | Adds hard suppression of the reasoning preamble |
| `cot` | Encourages reasoning but requires the JSON object last |

Concurrency 16, rate-limited to 35 rpm against a 40 rpm allowance — being
throttled costs more time than the unused headroom. Resumable: results append
as they complete and a rerun skips finished work.

## Scoring

A field is correct within **2% relative tolerance with a 0.1 absolute floor**.
The floor matters: judging 0.107g of salt on 2% of a tiny number would fail on
rounding that the panel itself performs.

**Kilojoule detection is by ratio, not assumption.** A returned energy value
within 3% of 4.184x the truth is the kilojoule figure — reproducible, and it
separates a systematic column error from ordinary noise.

**Panel style** is inferred from `countries_tags`: US and Canadian products are
per-serving by law, European ones per-100g.

## Agreement

For each field, the values returned by every variant that parsed. They *agree*
when all fall within tolerance of their own median. The consensus value is that
median, scored against the database.

This measures whether the model's own consistency predicts correctness — a
different question from which prompt is best, and a more useful one for anyone
deciding what to trust.
