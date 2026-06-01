# Marqo, YOLOX, CLIP, OWL-ViT로 위치 기반 이미지 검색과 open-vocabulary reranking 구현하기

이 문서는 이미지 전체를 검색하는 방식에서 한 단계 더 나아가, 이미지 안의 특정 객체나 영역을 찾기 위한 localization 전략을 설명합니다. Marqo의 이미지 검색에 객체 감지와 open-vocabulary reranking을 결합하면 "빨간 가방", "창가의 의자"처럼 이미지 안의 일부 영역에 집중한 검색을 만들 수 있습니다.

## 소개

일반 이미지 검색은 전체 이미지를 하나의 벡터로 표현합니다. 하지만 실제 검색에서는 이미지의 작은 영역이 더 중요할 때가 많습니다. 예를 들어 쇼핑 이미지에 모델, 배경, 여러 상품이 함께 있다면 전체 이미지 벡터만으로는 사용자가 찾는 상품을 정확히 구분하기 어렵습니다.

## 이미지 검색

기본 방식은 이미지를 Marqo에 문서로 추가하고, CLIP 계열 모델로 이미지 임베딩을 생성한 뒤 텍스트 또는 이미지 쿼리로 검색하는 것입니다.

## 이미지 검색과 localization

Localization은 이미지를 더 작은 영역으로 나누고, 각 영역을 별도 검색 대상으로 보거나 검색 시점에 다시 평가하는 방식입니다.

### 인덱싱 시점 분할

인덱싱 전에 이미지를 crop 또는 region 단위로 나누고, 각 영역을 별도 문서로 저장합니다. 검색 속도가 빠르고 단순하지만, 분할 품질이 검색 품질을 좌우합니다.

### 휴리스틱 분할

이미지를 격자로 자르거나 중심/가장자리 등 고정 규칙으로 나눕니다. 구현은 쉽지만 객체 위치를 정확히 반영하지 못할 수 있습니다.

### 모델 기반 분할

YOLOX 같은 객체 감지 모델로 bounding box를 찾고, 감지된 영역을 문서로 인덱싱합니다. 객체 중심 검색에는 더 적합하지만 전처리 비용이 늘어납니다.

## Reranking

초기 검색 결과를 가져온 뒤 OWL-ViT 같은 open-vocabulary 모델로 "쿼리와 실제 영역이 얼마나 맞는지" 다시 평가할 수 있습니다.

### 검색 시점 localization

이미지 전체로 먼저 후보를 찾고, 후보 이미지 안에서 쿼리와 맞는 영역을 찾아 reranking합니다. 인덱스 구조가 단순하고, 검색 시점에 더 유연하게 쿼리를 반영할 수 있습니다.

## 전체 흐름

### 이미지 데이터셋

예제는 이미지와 이미지 URL을 문서로 준비합니다. 객체 영역을 저장하려면 원본 이미지 ID와 crop 좌표를 함께 보관하는 것이 좋습니다.

### Marqo 시작

```bash
docker pull marqoai/marqo:latest
docker rm -f marqo
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:latest
```

### 문서 준비

```python
documents = [
    {
        "_id": "image-1",
        "image": "http://host.docker.internal:8222/image-1.jpg",
        "source": "example",
    }
]
```

### localization 인덱싱

```python
settings = {
    "model": "open_clip/ViT-B-32/laion2b_s34b_b79k",
    "treatUrlsAndPointersAsImages": True,
}

mq.create_index("localized-image-search", settings_dict=settings)
mq.index("localized-image-search").add_documents(
    documents,
    tensor_fields=["image"],
)
```

YOLOX 같은 모델로 crop을 생성했다면 crop URL과 bounding box를 별도 문서 필드로 저장합니다.

### 인덱싱 시점 localization 검색

```python
mq.index("localized-image-search").search("red backpack", limit=10)
```

검색 결과는 crop 문서 또는 원본 이미지 문서를 반환할 수 있습니다. crop 문서라면 원본 이미지 ID와 좌표를 사용해 UI에서 영역을 표시합니다.

### 검색 시점 localization

후보 이미지를 Marqo로 먼저 찾고, 후보마다 OWL-ViT로 쿼리와 맞는 영역을 평가합니다. 이 점수를 Marqo 점수와 조합해 rerank합니다.

## 결론

전체 이미지 검색은 빠르고 단순하지만, 이미지 안의 특정 영역을 찾는 요구에는 localization이 필요합니다. Marqo는 후보 검색과 벡터 저장을 담당하고, YOLOX/OWL-ViT 같은 모델은 영역 감지와 open-vocabulary reranking을 담당하도록 나누면 구현과 품질 사이의 균형을 잡을 수 있습니다.
