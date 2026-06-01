# Catalog Search Experiment

- Catalog: `examples\ImageSearchDemo\experiments\data\gift_url_products.csv`
- Model: `jinaai/jina-clip-v2`
- Documents: `79`
- Text queries: `13`
- Best text architecture: `text_vector_only` (MRR 0.8462, R@1 0.7692, P@5 0.5846)
- Best image architecture: `image_vector_only` (Exact@1 1.0, category R@5 1.0, mean self score 1.0)

## Text Search

| architecture | MRR | R@1 | R@5 | P@5 |
| --- | ---: | ---: | ---: | ---: |
| `fused_text_0.75_image_0.25` | 0.8341 | 0.7692 | 0.9231 | 0.5692 |
| `image_vector_cross_modal` | 0.5173 | 0.3077 | 0.7692 | 0.3231 |
| `text_vector_only` | 0.8462 | 0.7692 | 1.0 | 0.5846 |

## Image Search

| architecture | Exact@1 | Self MRR | Category R@5 | Mean self score | Min self score |
| --- | ---: | ---: | ---: | ---: | ---: |
| `image_vector_only` | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| `text_vector_only` | 0.6835 | 0.7827 | 0.962 | 0.3394 | 0.2007 |
