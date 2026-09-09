# Second Opinion

**Can a vision-language model read a nutrition panel — and can you tell when it has?**

*Four prompt variants. Where they agree, trust the answer. Where they don't, get a human.*

391 hand-labelled panels across 27 languages, drawn from 4.7 million Open Food
Facts products. Four prompt variants, 1,173 API calls, zero failures.

The short answer to the first question is *not well enough* — best accuracy was
58.4%. The useful answer is to the second one.

[**Live review queue**](https://second-opinion.streamlit.app) ·
[**Case study**](https://www.prathameshadarkar.com/second-opinion.html) ·
[Method](docs/method.md) · [Build journal](BUILD_JOURNAL.md)

---

## Why this matters

Nutrition data feeds carb counting for insulin dosing and sodium limits for
hypertension. If a model misreads a label, that error travels into tools people
use to make decisions. So the question is not only how often it is right, but
whether you can tell which outputs to trust.

## The headline

| | Fields | Accuracy |
|---|---:|---:|
| All four prompt variants agree | 1,267 · **49%** | **81.7%** |
| Variants disagree | 1,335 · 51% | 27.4% |

**A 54-point gap.** Where four cheap prompt variants concur, the answer is
usually right; where they don't, it usually isn't.

This is not an ensemble effect. Taking the median across variants scored **6.1
points worse** than the best single variant — they share a bias rather than
failing independently, so averaging cannot rescue them. But disagreement still
carries information: a panel that is blurry, oddly laid out, or requires
conversion pushes at least one variant off on its own.

**Agreement does not correct errors. It detects difficulty.**

### The deployable version

> Run several cheap prompt variants. Auto-accept the **49% of fields where they
> agree** at 82% accuracy; route the remaining **51%** to a reviewer.

That is an operating procedure with a known review rate rather than a benchmark
score. It is also not good enough for clinical use — 82% on the
high-confidence half would not be acceptable for insulin dosing. The finding is
that consensus gives you a usable confidence signal, not that the extractor is
ready.

## What a format constraint costs

| Variant | Parse rate | Fields correct | Output tokens |
|---|---:|---:|---:|
| `baseline` | 74.7% | 53.9% | 115,735 |
| `units` | 73.7% | 55.4% | 112,144 |
| `strict` | **96.9%** | 51.3% | **26,032** |
| `units-full` (full-res images) | 79.3% | **58.4%** | — |

`strict` adds one instruction — return JSON and nothing else. It raised the
parse rate 22 points and cut output tokens 77%, and made the model **worse**.

The mechanism is visible when you split by panel format:

| Variant | per-100g (EU) | per-serving (US) |
|---|---:|---:|
| `baseline` | 56.8% | 41.6% |
| `units` | 59.9% | 38.0% |
| `strict` | 56.8% | **27.4%** |
| `units-full` | **63.9%** | 37.0% |

European panels state values per 100g — read the number. American panels state
them per serving — read the number, read the serving size, convert.

`strict` costs **nothing** on panels needing no arithmetic and **29 points**
where conversion is required. The instruction removed the working space the
conversion needed.

> Don't suppress reasoning. Constrain where the answer goes.

## A named failure mode

European labels print energy twice: `180kJ / 42kcal`. The model returned **180
as kilocalories** — not a misread digit but the wrong column, a fourfold
overstatement recurring on every dual-unit panel.

Detected by ratio rather than by eye: any value within 3% of 4.184x the truth
is the kilojoule figure.

| Variant | kJ read as kcal |
|---|---:|
| `baseline` | **38.3%** of energy fields |
| `strict` | 25.6% |
| `units` | 12.0% |
| `units-full` | **10.4%** |

One added line of prompt guidance cut it by two thirds. `strict` contains that
same line and regresses to 25.6% — the model cannot apply the rule without room
to reason about it, which is the same mechanism again.

## The corpus

| Stage | Products | Share |
|---|---:|---:|
| In the September 2026 export | 4,733,607 | 100% |
| Carry a nutrition image | 2,308,131 | 48.8% |
| …recorded per 100g | 612,944 | 12.9% |
| …all seven nutrients present | 253,163 | 5.3% |
| Usable candidates | **242,597** | **5.1%** |

Half of Open Food Facts carries a nutrition photo, but only one product in
twenty is usable as a labelled extraction example. **The binding constraint is
not photography** — it is that three quarters of image-bearing products record
values per serving, where the stored figure and the printed one disagree by
construction.

Of 399 sampled images checked by eye, **97% were readable panels**. The
nutrition-image slot occasionally holds a front-of-pack photo instead; those
were excluded rather than counted as model failures.

## Running it

```bash
pip install -r requirements.txt
# download the export into data/ — see data/README.md

python3 build_sample.py --input data/openfoodfacts-products.jsonl.gz --n 400
python3 make_labeller.py --sample sample.jsonl && open label.html   # label by hand
python3 run_extraction.py --arm units --workers 16 --rpm 35
python3 score.py --arms baseline units strict units-full
python3 agreement.py --arms baseline units strict units-full
```

| Script | |
|---|---|
| `build_sample.py` | Streams the 12 GB export, filters, samples stratified by language |
| `make_labeller.py` | Builds a browser tool for labelling panel / partial / not-panel |
| `run_extraction.py` | Runs a prompt variant with concurrency, rate limiting, resume |
| `score.py` | Field accuracy, the kJ detector, language and panel-style splits |
| `agreement.py` | Ensemble and inter-variant confidence |
| `make_charts.py` | The figures |
| `app/` | The Streamlit review queue |

## Limitations

**The ground truth is crowdsourced.** Open Food Facts is self-reported. Several
products record salt at 0.01g per 100g against clearly legible label readings of
8–45g — the database is probably wrong in those cases, not the model. Until each
is checked by hand, every accuracy figure here is a **lower bound**.

**One model family.** A comparison against the 90B variant returned no
successful responses for this account; two alternative vision models were not
provisioned on it. Everything here describes Llama 3.2 11B Vision specifically.

**Language and panel format are confounded.** English scores worst of the major
languages — but English-language products here are disproportionately American,
and American panels are per serving. The apparent language effect is largely a
format effect and the two cannot be separated in this sample.

**The sample is stratified, not representative.** Equal numbers per language
give equal power to test language effects and make the sample unrepresentative
of the corpus, where English dominates. No corpus-level rate is quoted from it.

**Accuracy is measured; usefulness is not.** No reviewer has used the triage
rule in practice, and nothing here establishes that it saves anyone time.

## Source

[Open Food Facts](https://world.openfoodfacts.org/data), September 2026 export.
Product data under the Open Database License, images CC-BY-SA. Inference via
NVIDIA NIM using `meta/llama-3.2-11b-vision-instruct`.

An evaluation exercise. Not intended to support dietary or clinical decisions.

---

*By [Prathamesh Adarkar](https://www.prathameshadarkar.com). MIT licensed.*
