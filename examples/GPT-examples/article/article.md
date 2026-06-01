# "iron manual"에서 "ironman"까지 - 빠르게 수정 가능한 메모리로 GPT Q&A 강화하기

이 문서는 Marqo를 GPT 앞단의 검색 메모리로 사용해 두 가지 애플리케이션을 만드는 방법을 설명합니다.

## 1. 상품 Q&A - "Iron Manual"

첫 번째 사용 사례는 상품 설명서나 문서를 Marqo에 인덱싱하고, 사용자의 질문과 관련된 문서 조각을 찾아 GPT 컨텍스트로 넣는 것입니다. 모델은 검색된 문서를 근거로 답변하므로, 일반적인 추측보다 더 정확하고 업데이트 가능한 Q&A를 만들 수 있습니다.

## 2. 히스토리가 있는 NPC/채팅 에이전트 - "Ironman"

두 번째 사용 사례는 캐릭터의 배경, 과거 대화, 설정 정보를 Marqo에 저장하고, 대화마다 관련 기억을 검색해 응답에 반영하는 것입니다. 캐릭터의 메모리는 문서 추가/수정/삭제만으로 바꿀 수 있습니다.

## 소개

LLM은 많은 일반 지식을 갖고 있지만, 특정 제품 문서나 최신 정보, 게임 캐릭터의 개별 서사 같은 외부 지식은 따로 제공해야 합니다. Marqo는 이런 지식을 검색 가능한 벡터 메모리로 저장하고, 질문 시점에 관련 부분만 꺼내 GPT 프롬프트에 넣는 역할을 합니다.

## 사용 사례 1 - 상품 Q&A

### 1.1 상품 문서

상품 문서는 제목, 섹션, 본문, 문서 ID 같은 필드로 구성합니다.

```python
documents = [
    {
        "_id": "manual-1",
        "title": "Arc Reactor User Guide",
        "section": "Charging",
        "text": "Charge the device before first use...",
    }
]
```

### 1.2 문서 준비

긴 문서는 적절한 길이로 나눕니다. chunk가 너무 길면 컨텍스트 비용이 커지고, 너무 짧으면 의미가 부족할 수 있습니다.

### 1.3 문서 인덱싱

#### 1.3.1 Marqo 설치

```bash
docker pull marqoai/marqo:2.0.0
docker rm -f marqo
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:2.0.0
pip install marqo
```

#### 1.3.2 문서 추가

```python
import marqo

mq = marqo.Client(url="http://localhost:8882")
mq.create_index("product-manual")
mq.index("product-manual").add_documents(
    documents,
    tensor_fields=["title", "section", "text"],
)
```

#### 1.3.3 문서 검색

```python
results = mq.index("product-manual").search(
    "How do I charge the device?",
    limit=5,
)
```

## 1.4 Marqo와 GPT 연결

### 1.4.1 프롬프트 생성

검색 결과를 `Background`로 정리하고 질문을 붙입니다.

```text
Background:
Source 1: ...
Source 2: ...

Question:
How do I charge the device?

Answer:
```

### 1.4.2 컨텍스트 준비

Marqo hit에서 본문과 출처를 꺼내 프롬프트에 넣습니다. 출처 ID를 유지하면 답변 뒤에 근거를 표시할 수 있습니다.

### 1.4.3 토큰 인식 컨텍스트 자르기

모델 컨텍스트 길이를 넘지 않도록 검색 결과를 점수순으로 넣고, 필요한 경우 텍스트를 잘라냅니다.

### 1.4.4 GPT 추론

OpenAI API 키를 설정하고 GPT 호출 코드에서 위 프롬프트를 사용합니다.

```bash
export OPENAI_API_KEY="..."
```

### 1.4.5 출처 평가

답변에 사용한 출처를 함께 보여 주면 사용자가 결과를 검증하기 쉽습니다.

# 사용 사례 2 - 서사가 있는 대화형 에이전트

## 2.1 NPC 데이터 인덱싱

### 2.1.1 NPC 데이터

캐릭터 배경, 과거 사건, 대화 이력, 관계 정보를 문서로 저장합니다.

```python
npc_documents = [
    {
        "_id": "memory-1",
        "kind": "background",
        "text": "The character once saved a city from a failed experiment.",
    }
]
```

### 2.1.2 데이터 인덱싱

```python
mq.create_index("npc-memory")
mq.index("npc-memory").add_documents(
    npc_documents,
    tensor_fields=["text"],
)
```

## 2.2 Marqo와 GPT 연결

### 2.2.1 프롬프트 생성

캐릭터 말투, 역할, 금지 사항, 검색된 기억을 함께 넣습니다.

### 2.2.2 컨텍스트 준비

사용자 질문과 최근 대화를 Marqo 검색 쿼리로 사용해 관련 기억을 가져옵니다.

### 2.2.3 GPT 추론

검색된 배경 정보와 현재 대화 히스토리를 함께 넣어 응답을 생성합니다.

## 2.3 대화형으로 만들기

대화 루프는 다음 흐름을 반복합니다.

```python
# 사용할 배경 정보 개수
num_background = 5

# 사용자와 캐릭터의 응답 히스토리를 유지합니다.
history = []

# 질문과 관련된 배경을 검색합니다.
background = mq.index("npc-memory").search(question, limit=num_background)

# 컨텍스트 창에 맞게 텍스트를 줄입니다.
# LLM으로 응답을 생성합니다.
# 생성된 응답을 대화 히스토리에 추가합니다.
```

## 2.4 캐릭터 배경 수정

Marqo 문서를 추가, 수정, 삭제하면 캐릭터 메모리를 빠르게 바꿀 수 있습니다. 모델을 다시 학습하지 않아도 지식과 설정을 업데이트할 수 있다는 점이 이 구조의 장점입니다.

## 결론

Marqo를 GPT와 함께 사용하면 외부 지식을 검색 가능한 메모리로 만들 수 있습니다. 상품 Q&A에서는 근거 기반 답변을, 캐릭터 에이전트에서는 편집 가능한 장기 기억을 구현할 수 있습니다.
