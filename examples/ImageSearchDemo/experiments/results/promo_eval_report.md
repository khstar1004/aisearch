# Promo Search Experiment

- Model: `Qwen/Qwen3-VL-Embedding-2B`
- Documents: `27`
- Text queries: `13`
- Best text architecture: `text_vector_only` (MRR 1.0, R@1 1.0, P@5 0.4154)
- Best image architecture: `image_vector_only` (Exact@1 1.0, category R@5 1.0, mean self score 1.0014)

## Text Search

| architecture | MRR | R@1 | R@3 | R@5 | P@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `fused_text_0.25_image_0.75` | 0.8667 | 0.8462 | 0.8462 | 0.8462 | 0.3077 |
| `fused_text_0.50_image_0.50` | 1.0 | 1.0 | 1.0 | 1.0 | 0.3846 |
| `fused_text_0.75_image_0.25` | 1.0 | 1.0 | 1.0 | 1.0 | 0.4154 |
| `image_vector_cross_modal` | 0.722 | 0.6154 | 0.7692 | 0.8462 | 0.2462 |
| `multimodal_text_image_vector` | 1.0 | 1.0 | 1.0 | 1.0 | 0.4 |
| `text_vector_only` | 1.0 | 1.0 | 1.0 | 1.0 | 0.4154 |

## Image Search

| architecture | Exact@1 | Self MRR | Category R@5 | Mean self score | Min self score |
| --- | ---: | ---: | ---: | ---: | ---: |
| `image_vector_only` | 1.0 | 1.0 | 1.0 | 1.0014 | 0.9962 |
| `multimodal_text_image_vector` | 1.0 | 1.0 | 1.0 | 0.6067 | 0.296 |
| `text_vector_only` | 0.3333 | 0.5108 | 0.8148 | 0.2723 | 0.0905 |

## Recommendation

Use a split-vector index for production: Korean product metadata in a text vector field, product images in a separate image vector field, and route text and image searches to the matching field. Add score fusion or a reranker only after collecting click/search logs.
