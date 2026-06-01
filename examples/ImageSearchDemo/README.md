# Marqo 이미지 검색 데모

이 데모는 Marqo, Vespa, MIOC, Qwen 임베딩 서버를 함께 실행합니다. `gift.url.kr` 공개 판촉물 목록에서 수집한 상품 이미지 URL을 `Qwen/Qwen3-VL-Embedding-2B` 모델로 상품 텍스트와 이미지를 각각 별도 벡터로 임베딩하고, Marqo에는 `qwen_text_vector`, `qwen_image_vector` custom vector로 저장합니다. 한국어 텍스트 검색은 텍스트 벡터 필드만, 이미지-이미지 검색은 이미지 벡터 필드만 사용합니다.

Qwen 임베딩 서버는 Marqo의 기본 모델 추론 경로와 별도로 동작합니다. UI 서버가 Qwen에서 벡터를 만든 뒤 Marqo structured index에 custom vector로 넣기 때문에, 데모에서는 텍스트 검색 벡터와 이미지 검색 벡터를 분리한 구조를 직접 확인할 수 있습니다.

## 실행

저장소 루트에서 실행합니다.

```powershell
docker compose -f examples\ImageSearchDemo\compose-image-demo.yaml up --build --force-recreate -d
```

브라우저에서 엽니다.

```text
http://localhost:8110
```

첫 벡터화 실행에서는 Qwen3-VL-Embedding-2B 가중치를 `qwen-model-cache` Docker 볼륨에 다운로드하므로 시간이 걸릴 수 있습니다. 기본 설정은 `QWEN_DEVICE=cpu`입니다. GPU 여유 메모리가 충분하면 `QWEN_DEVICE=cuda`로 실행할 수 있습니다. Qwen 이미지의 기본 PyTorch wheel은 CUDA 12.8용이며, CPU 전용 이미지가 필요하면 `TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu` 빌드 인자를 사용합니다. 이후 실행에서는 캐시된 모델을 재사용합니다.

GPU로 실행하려면 같은 PowerShell 세션에서 아래처럼 실행합니다.

```powershell
$env:QWEN_DEVICE="cuda"
docker compose -f examples\ImageSearchDemo\compose-image-demo.yaml up --build --force-recreate -d
```

## UI 흐름

1. `Load 100 Products`를 클릭합니다. 현재 기본 CSV에는 한국 판촉물 79개가 들어 있습니다.
2. `Vectorize`를 클릭합니다.
3. `노란색 신발`, `파란 운동화`, `검은색 벨트` 같은 한국어 텍스트로 검색합니다.
4. 카드에서 `Use Image`를 클릭한 뒤 `Search By Image`로 이미지-이미지 검색을 실행합니다.

호스트에서 한국어 검색 스모크 테스트를 반복 실행할 수 있습니다.

```powershell
python examples\ImageSearchDemo\test_korean_search.py
```

판촉물 검색 구조 비교 실험은 아래 명령으로 실행합니다.

```powershell
python examples\ImageSearchDemo\experiments\promo_eval.py
```

공개 한국 판촉물 목록을 CSV로 수집한 뒤 같은 방식으로 평가할 수도 있습니다.

```powershell
python examples\ImageSearchDemo\experiments\crawl_gift_url.py --pages 5 --max-products 80
python examples\ImageSearchDemo\experiments\catalog_eval.py --catalog-csv examples\ImageSearchDemo\experiments\data\gift_url_products.csv --max-docs 79
```

현재 모델/검색 구조 판단은 `examples\ImageSearchDemo\experiments\MODEL_DECISION.md`에 정리되어 있습니다.

Jina CLIP v2 같은 후보 모델을 비교할 때는 Qwen 기본 이미지와 의존성이 다를 수 있습니다. Jina 실험은 `docker compose build --build-arg TRANSFORMERS_PACKAGE="transformers<5" qwen`, `QWEN_MODEL_NAME=jinaai/jina-clip-v2`, `QWEN_EMBEDDING_DIMENSIONS=1024`, `--query-prompt-name retrieval.query`, `--skip-multimodal` 조합으로 별도 평가하고, 데모를 다시 쓸 때는 기본 Qwen 이미지로 재빌드합니다.

## 중지

```powershell
docker compose -f examples\ImageSearchDemo\compose-image-demo.yaml down --remove-orphans
```
