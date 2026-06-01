# Korean Promotional Product Search Decision

## Current Best Tested Stack

- Embedding model: `Qwen/Qwen3-VL-Embedding-2B`
- Vector database/search engine: Marqo API over Vespa
- Index type: structured custom-vector index
- Text search field: `qwen_text_vector`
- Image search field: `qwen_image_vector`

This is the best tested local combination so far for a Korean promotional-product search experience.

## Why Split Vectors

Promotional product search has two different retrieval intents:

- Text-to-product search: Korean query such as `로고 인쇄 볼펜`, `판촉용 머그컵`, `USB 메모리`
- Image-to-image search: visually similar product retrieval, duplicate detection, and design/reference matching

A single mixed `text+image` vector weakens image exact/similarity behavior. The split-vector structure lets each search route use the right representation:

- Text query -> `qwen_text_vector`
- Image query -> `qwen_image_vector`

## Local Experiment

Experiment script:

```powershell
python examples\ImageSearchDemo\experiments\promo_eval.py
```

Output:

- `examples/ImageSearchDemo/experiments/results/promo_eval_results.json`
- `examples/ImageSearchDemo/experiments/results/promo_eval_report.md`

Dataset:

- 27 promotional-product proxy images
- 13 Korean product-search queries
- Categories include pens, mugs, bottles, umbrellas, tote bags, USB drives, towels, notebooks, lanyards, keychains, calendars, mousepads, and sticky notes

Qwen split-vector result:

| task | best structure | key result |
| --- | --- | --- |
| Korean text search | `text_vector_only` | MRR `1.0`, Recall@1 `1.0`, Precision@5 `0.4154` |
| Image search | `image_vector_only` | Exact@1 `1.0`, category Recall@5 `1.0`, mean self score `1.0014` |

Alternative structures:

| structure | text MRR | text R@1 | image Exact@1 | image mean self score |
| --- | ---: | ---: | ---: | ---: |
| `text_vector_only` | `1.0` | `1.0` | `0.3333` | `0.2723` |
| `image_vector_only` for text | `0.722` | `0.6154` | `1.0` | `1.0014` |
| `text+image multimodal` | `1.0` | `1.0` | `1.0` | `0.6067` |
| split routing | `1.0` | `1.0` | `1.0` | `1.0014` |

The deciding signal is image similarity score. The old mixed-vector structure can still find the same image, but its self-score is much lower. The split image vector gives near-exact self similarity.

## Marqo Ecommerce Baseline

The same proxy dataset was also tested with `Marqo/marqo-ecommerce-embeddings-L` through Marqo's native model path.

Observed result:

| task | result |
| --- | ---: |
| Korean text search Recall@1 | `0.1538` |
| Korean text search Recall@5 | `0.3846` |
| Image search Exact@1 | `1.0` |
| Image search category Recall@5 | `1.0` |
| Avg image search latency | `1472.8 ms` |

Interpretation:

- It is strong enough for image-to-image product matching.
- It is not the current best choice for Korean text search without a Korean query normalization or translation layer.
- Qwen is the better single-model choice for Korean text + image search in this local test.

## Production Search Structure

Recommended first production structure:

1. Store normalized product fields: Korean title, category, option names, material, color, print method, brand/event tags.
2. Store `qwen_text_vector` from Korean product metadata.
3. Store `qwen_image_vector` from product image only.
4. Keep lexical fields (`title`, `category`, `tags`) for filters, exact keyword fallback, and future hybrid search.
5. Route query types explicitly:
   - Korean text query: vector search on `qwen_text_vector`
   - Uploaded/reference image query: vector search on `qwen_image_vector`
   - Same/near-duplicate image: threshold on image-vector score
6. Add click-log based reranking only after collecting real site search logs.

Suggested image score interpretation for the current Qwen image field:

- `>= 0.98`: likely same or near-duplicate image
- `0.80 - 0.98`: visually very similar product family
- `< 0.80`: semantic/category similarity may still be useful, but inspect by category

These thresholds are local-demo values and should be recalibrated on the real product catalog.

## Next Model Candidates

The next candidates worth testing further on a larger real Korean promotional-product catalog are:

- `Qwen/Qwen3-VL-Embedding-8B`: tested on the 79-product catalog. It improved image self-score and text P@5 slightly, but did not beat 2B on text MRR or Recall@1.
- `jinaai/jina-clip-v2`: tested on the same 79-product Korean catalog; useful as a multilingual CLIP baseline, but it did not beat Qwen 2B on Korean text search.
- `Marqo/marqo-ecommerce-embeddings-L`: strong ecommerce image model, but needs Korean text handling or translation.

Do not switch away from the current Qwen 2B split structure until a candidate beats it on the same Korean catalog evaluation at the target operating point.

## Real Korean Catalog Smoke Test

Additional scripts:

```powershell
python examples\ImageSearchDemo\experiments\crawl_gift_url.py --pages 5 --max-products 80
python examples\ImageSearchDemo\experiments\catalog_eval.py --catalog-csv examples\ImageSearchDemo\experiments\data\gift_url_products.csv --max-docs 79
```

Input source:

- `https://gift.url.kr/`
- 79 products crawled from public Korean promotional-product listing pages
- Example categories: `가정/생활용품`, `우산/우의`, `컵제품`, `사무용잡화`, `수건/손수건`, `필기구`, `휴대폰용품`

Result:

| task | best structure | key result |
| --- | --- | --- |
| Korean category text search | `text_vector_only` | MRR `0.9231`, Recall@1 `0.8462`, Recall@5 `1.0`, Precision@5 `0.6769` |
| Image search | `image_vector_only` | Exact@1 `1.0`, category Recall@5 `1.0`, mean self score `1.0006` |

Structure comparison on the real catalog sample:

| structure | text MRR | text R@1 | text P@5 | image Exact@1 | image mean self score |
| --- | ---: | ---: | ---: | ---: | ---: |
| `text_vector_only` | `0.9231` | `0.8462` | `0.6769` | `0.6203` | `0.3193` |
| `image_vector_cross_modal` | `0.3768` | `0.1538` | `0.2308` | `1.0` | `1.0006` |
| `text+image multimodal` | `0.8654` | `0.7692` | `0.5538` | `1.0` | `0.7027` |
| split routing | `0.9231` | `0.8462` | `0.6769` | `1.0` | `1.0006` |

This real-catalog smoke test reinforces the same decision: keep text and image vectors separate. Text-only metadata vectors are best for Korean product/category queries, and image-only vectors are best for same/similar image retrieval.

## Jina CLIP v2 Catalog Comparison

Jina CLIP v2 was evaluated on the same crawled Korean catalog with:

```powershell
python examples\ImageSearchDemo\experiments\catalog_eval.py `
  --catalog-csv examples\ImageSearchDemo\experiments\data\gift_url_products.csv `
  --max-docs 79 `
  --min-docs-per-category 2 `
  --max-categories 16 `
  --qwen-url http://localhost:8111 `
  --model-label jinaai/jina-clip-v2 `
  --query-prompt-name retrieval.query `
  --skip-multimodal `
  --output-dir examples\ImageSearchDemo\experiments\results\jina_clip_v2
```

Jina requires a different local dependency set from the Qwen demo image. In this environment it ran with `TRANSFORMERS_PACKAGE="transformers<5"` and 1024-dimensional vectors. That dependency set should not be used for the default Qwen demo image because it degraded Qwen query/vector compatibility in smoke tests.

Result:

| model | best text structure | text MRR | text R@1 | text P@5 | image Exact@1 | image mean self score |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `Qwen/Qwen3-VL-Embedding-2B` | `text_vector_only` | `0.9231` | `0.8462` | `0.6769` | `1.0` | `1.0006` |
| `jinaai/jina-clip-v2` | `text_vector_only` | `0.8462` | `0.7692` | `0.5846` | `1.0` | `1.0` |

Conclusion: Jina CLIP v2 is viable for image-image retrieval and multilingual cross-modal experiments, but the current best tested local stack remains Qwen 2B with split `qwen_text_vector` and `qwen_image_vector` fields.

## Qwen 8B Catalog Comparison

Qwen 8B was evaluated on the same catalog with 4096-dimensional vectors:

```powershell
python examples\ImageSearchDemo\experiments\catalog_eval.py `
  --catalog-csv examples\ImageSearchDemo\experiments\data\gift_url_products.csv `
  --max-docs 79 `
  --min-docs-per-category 2 `
  --max-categories 16 `
  --qwen-url http://localhost:8111 `
  --model-label Qwen/Qwen3-VL-Embedding-8B `
  --output-dir examples\ImageSearchDemo\experiments\results\qwen_8b
```

Result:

| model | best text structure | text MRR | text R@1 | text P@5 | image Exact@1 | image mean self score |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `Qwen/Qwen3-VL-Embedding-2B` | `text_vector_only` | `0.9231` | `0.8462` | `0.6769` | `1.0` | `1.0006` |
| `Qwen/Qwen3-VL-Embedding-8B` | `text_vector_only` | `0.8846` | `0.7692` | `0.7231` | `1.0` | `1.0024` |

Interpretation: 8B is stronger by official benchmark and gives a slightly higher same-image score locally, but it costs much more VRAM and cold-start time. For this demo and this Korean catalog sample, 2B remains the best balanced default. If the production priority becomes pure image matching and the catalog is large enough to make the small image-score gain meaningful, retest 8B on production images before switching.
