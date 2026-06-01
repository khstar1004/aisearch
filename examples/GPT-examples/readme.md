원문 글: [From iron manual to ironman](https://www.marqo.ai/blog/from-iron-manual-to-ironman-augmenting-gpt-with-marqo-for-fast-editable-memory-to-enable-context-aware-question-answering)

# 사전 준비

1. OpenAI API 키가 필요합니다.

```bash
export OPENAI_API_KEY="..."
```

2. Marqo를 설치하고 실행합니다.

```bash
docker pull marqoai/marqo:2.0.0
docker rm -f marqo
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:2.0.0
pip install marqo
```

3. 기타 의존성을 설치합니다.

```bash
pip install -r requirements.txt
```

# 1. 상품 Q&A

```bash
python product_q_n_a.py
```

# 2. 히스토리/NPC가 있는 채팅 에이전트

```bash
python ironman.py
```
