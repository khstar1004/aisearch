# Marqo로 주제별 뉴스 요약 만들기

이 예제는 Marqo 검색 결과를 GPT 프롬프트의 컨텍스트로 넣어, 특정 날짜의 뉴스에 근거한 요약 답변을 만드는 흐름을 보여 줍니다. 검색으로 관련 문서를 먼저 가져오고, 생성 모델은 그 문서 안의 사실을 바탕으로 답변합니다. 이 방식은 일반적으로 retrieval-augmented generation, 즉 RAG라고 부릅니다.

## 핵심 아이디어

일반적인 GPT 질문만으로는 "오늘 비즈니스에서 무슨 일이 일어나고 있나요?" 같은 질문에 최신 또는 특정 날짜의 사실을 정확히 답하기 어렵습니다. Marqo에 뉴스 문서를 인덱싱한 뒤 질문과 날짜로 검색하면, 생성 모델에 넣을 수 있는 관련 뉴스 조각을 얻을 수 있습니다.

흐름은 다음과 같습니다.

1. 예제 뉴스 문서를 Marqo 인덱스에 저장합니다.
2. 사용자의 질문과 날짜 필터로 관련 뉴스를 검색합니다.
3. 검색 결과를 프롬프트의 `Background`로 넣습니다.
4. GPT가 검색된 근거를 사용해 답변을 생성합니다.

## Marqo 실행

```bash
docker pull marqoai/marqo:latest
docker rm -f marqo
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:latest
```

## 뉴스 문서 형태

예제 코퍼스는 BBC와 Reuters의 뉴스 콘텐츠를 사용합니다. 각 문서는 Marqo 문서 ID, 날짜, 웹사이트, 제목, 본문을 포함합니다.

```python
[
    {
        "_id": "2",
        "date": "2022-11-09",
        "website": "www.bbc.co.uk",
        "Title": "COP27: Time to pay the climate bill - vulnerable nations",
        "Description": "Leaders of countries flooded or parched due to climate change are pleading at the COP27 summit..."
    }
]
```

## 문서 인덱싱

Marqo는 기본적으로 텍스트 임베딩 기반 검색과 lexical/metadata 검색을 함께 제공합니다.

```python
from news import MARQO_DOCUMENTS

DOC_INDEX_NAME = "news-index"

print("Marqo client에 연결합니다.")
mq = marqo.Client(url="http://localhost:8882")

print("Marqo 인덱스를 생성합니다.")
mq.create_index(DOC_INDEX_NAME)

print("문서를 인덱싱합니다.")
mq.index(DOC_INDEX_NAME).add_documents(
    MARQO_DOCUMENTS,
    tensor_fields=["Title", "Description"],
)
```

## 질문과 날짜로 검색

```python
question = "What is happening in business today?"
date = "2022-11-09"

results = mq.index(DOC_INDEX_NAME).search(
    q=question,
    filter_string=f"date:{date}",
    limit=5,
)
```

## GPT 프롬프트에 검색 결과 연결

검색 결과의 제목과 본문을 `Background`로 넣고, 그 아래 사용자의 질문을 붙입니다. 이렇게 하면 모델은 일반적인 추측이 아니라 검색된 기사에 근거해 답변할 수 있습니다.

예를 들어 M&S 비용 압박, Meta 감원, Tesla 주가 하락 같은 검색 결과를 컨텍스트에 넣으면, 답변도 해당 사실을 중심으로 생성됩니다.

## 실행

OpenAI API 토큰을 설정한 뒤 예제 스크립트를 실행합니다.

```bash
export OPENAI_API_KEY="..."
python main.py
```

전체 코드는 `main.py`를 참고하세요.
