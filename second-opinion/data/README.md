# The data

`build_sample.py` expects the Open Food Facts bulk export here:

```
data/openfoodfacts-products.jsonl.gz
```

~12 GB, not committed. Download it from
[world.openfoodfacts.org/data](https://world.openfoodfacts.org/data).

**Use the bulk export, not the API.** Open Food Facts rate-limits product reads
to 15 per minute and asks people building datasets to download instead. The
scripts stream the gzip and never decompress it.

Set a custom User-Agent on anything that does hit the API, in the form
`AppName/Version (contact@email)`, and fill in their
[API usage form](https://world.openfoodfacts.org/).

## Licensing

Product data is under the Open Database License; images are CC-BY-SA. Nothing
is redistributed in this repository — products are referenced by barcode and
images are linked, not copied.

## Your numbers will differ

Open Food Facts is continuously edited. A download taken later will not
reproduce the figures in the README: products are added, nutrition values are
corrected, and images are re-uploaded with new revision numbers, which changes
the constructed image URLs.

Every figure here describes the **September 2026 export**.
