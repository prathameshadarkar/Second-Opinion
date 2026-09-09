# Build journal

What was decided and why, including the parts that went wrong. Written as the
build happened.

---

## 1. The data source was wrong twice

The plan called for USDA FoodData Central: structured nutrition data, public
domain, ideal ground truth. **It has no images.** Checking the API guide before
writing code saved a wasted afternoon.

Open Food Facts has both — but the images are CC-BY-SA and the data ODbL, so
nothing is redistributed here; the repo references products by barcode.

## 2. The bulk export is not the API

The first filter looked for `image_nutrition_url`, which the API returns. After
a million products, **zero matches**. That field is computed by the API and does
not exist in the dump, which instead carries an `images` object with entries
keyed `nutrition_en`, `nutrition_fr` and so on.

Image URLs have to be constructed:

```
{base}/{code split into 3/3/3/rest}/{key}.{rev}.400.jpg
```

Verified against two known-good URLs before rerunning. Both schemas seen in the
dump — a flat `images["nutrition_en"]` and a newer
`images["selected"]["nutrition"]["en"]` — are handled.

**Lesson:** inspect the actual file before filtering on assumed field names. A
scan that returns zero after a million rows is a schema problem, not a strict
filter.

## 3. Label the images before spending anything

The nutrition-image slot is crowdsourced and does not always hold a panel — the
first product inspected, Nutella, had a photo of the jar in it.

So all 399 sampled images were checked by eye before a single API call. **97%
were readable panels**; the rest were excluded rather than counted as model
failures. Building the labelling tool cost an hour and prevented every
subsequent accuracy figure from being contaminated by images no human could
read either.

That result was also weaker than expected. The Nutella case suggested this
would be a major finding; at 1.3% not-panel it is a footnote. The interesting
constraint turned out to be elsewhere — 73% of image-bearing products are
recorded per serving.

## 4. The smoke test earned its keep

One product, one call, before building anything. It surfaced three things:

- the model **can** read panels — fat, saturated fat, protein and salt correct
- it returned **180 kcal** where the truth was 42, having read the kilojoule
  column of a dual-unit panel
- it ignored "return only JSON", wrote five steps of reasoning, and hit the
  400-token cap mid-object

Two were fixable by tweaking. The third became the project's main finding.

## 5. Three arms, then a fourth

`baseline`, `units` (adds a dual-unit warning) and `strict` (adds hard JSON-only
output). `strict` won on parse rate by 22 points, cut output tokens 77%, and
**lost accuracy** — concentrated entirely on per-serving panels, from 41.6% to
27.4%, while per-100g panels were untouched at 56.8%.

That isolates the mechanism: the constraint removed the model's working space,
and only the panels needing arithmetic cared.

Which implies a fourth arm the first three did not test — reason freely, but put
the JSON last. That is `cot`.

## 6. A silent bug in the runner

Output files were named by arm and image size but **not by model**. A run against
the 90B variant wrote to the same file as the 11B run, saw every barcode already
present, and exited having done nothing — with a cheerful "nothing to do".

Fixed by putting the model in the filename. Silent no-ops are worse than crashes;
this one could have produced a "comparison" between a model and itself.

## 7. The model comparison failed, and that is in the writeup

The 90B variant timed out on every call — 70 minutes, zero successes. Two
alternatives 404'd. Querying `/v1/models` showed why: the catalogue lists
NVIDIA's whole range, but the account is provisioned for a subset. The 90B is
provisioned and capacity-constrained; the others are not provisioned at all.

Recorded as a limitation rather than chased further.

## 8. Parsing improvements applied retroactively

The `cot` arm needed a parser that takes the **last** balanced JSON object
rather than the outermost, because reasoning text can itself contain braces.

Since raw responses are stored, `score.py` re-parses them at scoring time — so
the improvement applies to runs already completed without spending another API
call. Re-parsing recovered responses previously counted as failures, which
matters because the parse rate is what the whole trade-off rests on.

## 9. The ensemble did not work, which was the better outcome

Median across variants scored 6.1 points **below** the best single variant. The
variants share a bias rather than failing independently.

But agreement still predicted correctness — 81.7% versus 27.4%. Those two facts
look contradictory and are not: averaging cannot fix a shared error, while
disagreement still flags the hard cases. Consensus detects difficulty rather
than correcting mistakes, and that distinction is what makes it usable.

## 10. What is still open

**The disagreement review.** 327 cases where model and database differ. The
extremes look like database errors — salt at 0.01g per 100g against legible
readings of 8–45g. Until those are checked by hand, every accuracy figure is a
lower bound and the ground-truth error rate is unmeasured.

**Cropping to the panel.** Full-resolution images beat 400px thumbnails by three
points; the images still contain the whole package. Cropping is the most likely
remaining accuracy gain and is untested.
