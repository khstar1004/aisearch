# "Context Is All You Need" - 개인화 멀티모달 벡터 검색

이 문서는 Marqo로 텍스트, 이미지, 사용자 맥락을 함께 사용하는 멀티모달 검색을 구성하는 방법을 설명합니다. 핵심은 검색 쿼리를 단순 문자열이 아니라 "의도와 맥락을 가진 벡터 입력"으로 보는 것입니다.

## 1. 멀티모달 검색 소개

전통적인 검색은 텍스트 필드와 키워드 일치에 크게 의존합니다. 멀티모달 검색은 텍스트, 이미지, 상품 속성, 사용자 선호 같은 여러 신호를 같은 검색 흐름에서 다룹니다. 예를 들어 사용자는 "하이킹에 어울리는 초록색 백팩"처럼 텍스트로 찾을 수도 있고, 마음에 드는 상품 이미지를 기준으로 비슷한 상품을 찾을 수도 있습니다.

Marqo는 CLIP 계열 모델과 벡터 검색을 사용해 이런 입력을 하나의 검색 경험으로 연결합니다.

### 1.1 멀티모달 검색

멀티모달 검색은 서로 다른 데이터 타입을 같은 의미 공간에 배치합니다. 이미지와 텍스트가 같은 벡터 공간에서 비교되므로 다음과 같은 검색이 가능합니다.

- 텍스트로 이미지 검색
- 이미지로 이미지 검색
- 텍스트와 이미지 조합 검색
- 사용자 선호 이미지들을 하나의 컨텍스트 벡터로 만든 개인화 검색

### 1.2 장점

- 사용자가 정확한 상품명이나 키워드를 몰라도 의도에 가까운 결과를 찾을 수 있습니다.
- 이미지 품질, 선호도, 인기도 같은 추가 신호를 랭킹에 반영할 수 있습니다.
- 상품, 콘텐츠, 추천 시스템에서 검색과 개인화를 같은 인덱스 위에서 구현할 수 있습니다.

## 2. 실전 검색 패턴

### 2.1 멀티모달 쿼리

텍스트와 이미지를 함께 사용해 검색 의도를 표현합니다. 예를 들어 "검은색 재킷"이라는 텍스트와 사용자가 고른 상품 이미지를 함께 반영할 수 있습니다.

### 2.2 부정 조건

"파란색은 제외", "가죽 느낌은 빼고" 같은 조건은 필터나 쿼리 설계로 처리합니다. 의미 검색은 유사성을 찾는 데 강하지만, 제외 조건은 명확한 필터와 함께 쓰는 것이 안정적입니다.

### 2.3 낮은 품질 이미지 제외

이미지 품질, 미학 점수, 상품 상태 같은 메타데이터를 문서에 함께 저장하면 검색 후 랭킹 또는 필터링에 활용할 수 있습니다.

### 2.4 이미지로 검색

이미지 URL이나 포인터를 쿼리로 사용하면 비슷한 이미지를 찾을 수 있습니다. 이 방식은 쇼핑몰에서 "비슷한 상품 찾기" 기능을 만들 때 유용합니다.

### 2.5 인기 상품 또는 좋아요 기반 조건 검색

사용자가 좋아한 상품, 많이 본 상품, 장바구니 상품을 벡터 컨텍스트로 만들어 검색에 반영할 수 있습니다.

### 2.6 검색을 프롬프팅처럼 다루기

검색 쿼리는 단순 키워드보다 프롬프트에 가깝게 작성할 수 있습니다. "여름 여행에 어울리는 가볍고 밝은 색상의 백팩"처럼 의도와 상황을 자연어로 표현하면 모델이 의미적으로 가까운 결과를 찾습니다.

### 2.7 추가 신호로 랭킹

벡터 유사도만으로 충분하지 않은 경우 가격, 인기, 재고, 품질 점수 같은 수치 필드를 score modifier로 결합합니다.

### 2.8 멀티모달 엔티티

상품 하나가 이미지, 제목, 설명, 카테고리, 태그를 함께 가질 수 있습니다. 각 필드를 어떻게 임베딩하고 결합할지 mappings로 제어합니다.

## 3. 상세 예제

### 3.1 데이터셋

예제는 상품 이미지와 메타데이터를 사용합니다. 각 문서는 이미지 URL, 설명, 카테고리, 품질 또는 선호도 점수 같은 필드를 가질 수 있습니다.

### 3.2 Marqo 설치

```bash
pip install marqo
docker pull marqoai/marqo:latest
docker rm -f marqo
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:latest
```

### 3.3 데이터 로딩

```python
import marqo

mq = marqo.Client(url="http://localhost:8882")
```

이미지를 Docker 컨테이너에서 접근할 수 있도록 URL 형태로 준비합니다.

### 3.4 인덱스 생성

```python
settings = {
    "model": "open_clip/ViT-B-32/laion2b_s34b_b79k",
    "treatUrlsAndPointersAsImages": True,
}

mq.create_index("multimodal-products", settings_dict=settings)
```

### 3.5 이미지 추가

```python
mq.index("multimodal-products").add_documents(
    documents,
    tensor_fields=["image", "title", "description"],
)
```

### 3.6 검색

```python
mq.index("multimodal-products").search("a waterproof hiking backpack")
```

### 3.7 프롬프트형 검색

```python
mq.index("multimodal-products").search(
    "a lightweight bag for a weekend hiking trip, practical and not too formal"
)
```

### 3.8 의미 필터

메타데이터 필터를 조합해 검색 범위를 줄입니다.

```python
mq.index("multimodal-products").search(
    "minimal black backpack",
    filter_string="category:bag",
)
```

### 3.9 부정 조건

부정 조건은 가능한 한 명시적 필터로 표현합니다. 색상, 카테고리, 브랜드처럼 구조화된 필드는 문서 필드로 저장해 필터링합니다.

### 3.10 이미지 검색

```python
mq.index("multimodal-products").search("http://host.docker.internal:8222/backpack.jpg")
```

### 3.11 멀티모달 쿼리

여러 이미지 또는 텍스트/이미지 조합으로 사용자 취향을 표현할 수 있습니다. 실제 구현에서는 먼저 컨텍스트 벡터를 만들고, 이를 검색 쿼리에 반영합니다.

### 3.12 랭킹

```python
mq.index("multimodal-products").search(
    "premium leather bag",
    score_modifiers={
        "multiply_score_by": [{"field_name": "popularity", "weight": 0.2}],
    },
)
```

### 3.13 선호 상품 기반 검색

좋아요 또는 구매 이력을 작은 컨텍스트 인덱스로 만들고, 그 벡터를 검색에 사용할 수 있습니다. 이를 통해 "이 사용자가 좋아할 만한 비슷한 상품" 검색을 만들 수 있습니다.

### 3.14 멀티모달 객체 인덱싱

상품의 여러 필드를 하나의 멀티모달 객체로 보고, 필드별 가중치와 임베딩 여부를 mappings로 조정합니다.

## 4. 결론

Marqo의 강점은 텍스트 검색, 이미지 검색, 필터링, 랭킹, 개인화를 하나의 검색 파이프라인에서 결합할 수 있다는 점입니다. 상품 검색에서는 특히 "정확한 키워드"보다 "상황과 의도"가 중요한 경우가 많으므로, 멀티모달 검색은 더 자연스러운 탐색 경험을 만드는 데 유용합니다.
