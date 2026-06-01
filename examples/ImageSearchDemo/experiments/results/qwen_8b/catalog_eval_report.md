# Catalog Search Experiment

- Catalog: `examples\ImageSearchDemo\experiments\data\gift_url_products.csv`
- Model: `Qwen/Qwen3-VL-Embedding-8B`
- Documents: `79`
- Text queries: `13`
- Best text architecture: `text_vector_only` (MRR 0.8846, R@1 0.7692, P@5 0.7231)
- Best image architecture: `image_vector_only` (Exact@1 1.0, category R@5 1.0, mean self score 1.0024)

## Text Search

| architecture | MRR | R@1 | R@5 | P@5 |
| --- | ---: | ---: | ---: | ---: |
| `fused_text_0.75_image_0.25` | 0.8718 | 0.7692 | 1.0 | 0.6769 |
| `image_vector_cross_modal` | 0.6295 | 0.4615 | 0.8462 | 0.3231 |
| `multimodal_text_image_vector` | 0.8846 | 0.7692 | 1.0 | 0.5846 |
| `text_vector_only` | 0.8846 | 0.7692 | 1.0 | 0.7231 |

## Image Search

| architecture | Exact@1 | Self MRR | Category R@5 | Mean self score | Min self score |
| --- | ---: | ---: | ---: | ---: | ---: |
| `image_vector_only` | 1.0 | 1.0 | 1.0 | 1.0024 | 0.9929 |
| `multimodal_text_image_vector` | 1.0 | 1.0 | 1.0 | 0.6297 | 0.4658 |
| `text_vector_only` | 0.6835 | 0.8057 | 0.9873 | 0.306 | 0.1538 |
