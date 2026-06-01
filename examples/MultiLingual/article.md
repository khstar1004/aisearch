# Marqo로 다국어 법률 데이터베이스 만들기

이 예제는 Marqo를 사용해 여러 언어의 법률 문서를 같은 인덱스에 저장하고, 다른 언어의 질문으로도 관련 문서를 찾는 proof of concept입니다.

## 데이터셋

예제 데이터는 유럽 법률 문서처럼 긴 텍스트와 여러 언어가 섞인 자료를 가정합니다. 문서에는 제목, 본문, 언어, 출처 같은 메타데이터가 포함될 수 있습니다.

## proof of concept 범위

이 예제의 목표는 완전한 법률 검색 제품을 만드는 것이 아니라 다음 흐름을 검증하는 것입니다.

- 다국어 임베딩 모델로 문서를 인덱싱합니다.
- 영어가 아닌 질문도 같은 의미 공간에서 검색합니다.
- 검색 결과의 출처와 본문 일부를 확인합니다.
- 필요한 경우 언어, 문서 유형, 관할권 같은 필드로 필터링합니다.

## 해결 방식

Marqo를 실행하고 클라이언트를 준비합니다.

```python
import marqo
from pprint import pprint

mq = marqo.Client(url="http://localhost:8882")
```

다국어 검색에 적합한 모델을 설정해 인덱스를 만듭니다.

```python
settings = {
    "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
}

mq.create_index("eu-legal", settings_dict=settings)
```

문서를 추가합니다.

```python
documents = [
    {
        "_id": "doc-1",
        "title": "Regulation example",
        "text": "The legal text goes here...",
        "language": "en",
        "source": "eu",
    }
]

mq.index("eu-legal").add_documents(
    documents,
    tensor_fields=["title", "text"],
)
```

## 인덱스 검색

`pprint`는 Python 기본 formatter로, 검색 결과를 읽기 좋게 출력합니다.

```python
results = mq.index("eu-legal").search(
    "어업 규제와 관련된 조항",
    limit=5,
)

pprint(results)
```

언어 또는 출처 필터를 함께 사용할 수 있습니다.

```python
results = mq.index("eu-legal").search(
    "fishing restrictions",
    filter_string="language:en",
    limit=5,
)
```

## 결론

Marqo를 사용하면 다국어 문서를 별도 언어별 검색 시스템으로 나누지 않고 하나의 의미 검색 인덱스에서 다룰 수 있습니다. 법률 도메인에서는 정확성 검증, 출처 표시, 최신성 관리가 반드시 필요하지만, 다국어 검색과 RAG 컨텍스트 검색의 기반으로는 간단하고 실용적인 구조를 제공합니다.
