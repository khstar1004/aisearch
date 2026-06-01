# Catalog Search Experiment

- Catalog: `examples\ImageSearchDemo\experiments\data\gift_url_products.csv`
- Model: `Qwen/Qwen3-VL-Embedding-2B`
- Documents: `79`
- Text queries: `13`
- Best text architecture: `text_vector_only` (MRR 0.9231, R@1 0.8462, P@5 0.6769)
- Best image architecture: `image_vector_only` (Exact@1 1.0, category R@5 1.0, mean self score 1.0006)

## Text Search

| architecture | MRR | R@1 | R@5 | P@5 |
| --- | ---: | ---: | ---: | ---: |
| `fused_text_0.75_image_0.25` | 0.8462 | 0.6923 | 1.0 | 0.6615 |
| `image_vector_cross_modal` | 0.3768 | 0.1538 | 0.6923 | 0.2308 |
| `multimodal_text_image_vector` | 0.8654 | 0.7692 | 1.0 | 0.5538 |
| `text_vector_only` | 0.9231 | 0.8462 | 1.0 | 0.6769 |

## Image Search

| architecture | Exact@1 | Self MRR | Category R@5 | Mean self score | Min self score |
| --- | ---: | ---: | ---: | ---: | ---: |
| `image_vector_only` | 1.0 | 1.0 | 1.0 | 1.0006 | 0.9961 |
| `multimodal_text_image_vector` | 1.0 | 1.0 | 1.0 | 0.7027 | 0.4983 |
| `text_vector_only` | 0.6203 | 0.7314 | 0.9747 | 0.3193 | 0.1708 |
