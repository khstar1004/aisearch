# 음성 데이터 검색

원문 글: [speech processing](https://www.marqo.ai/blog/speech-processing)

## 개요

이 작은 프로젝트는 오디오 파일의 전사 텍스트를 Marqo에 인덱싱해 검색 가능한 데이터베이스를 만듭니다. 사용자는 텍스트로 질의하고, 시스템은 해당 질문과 관련된 오디오 구간을 반환할 수 있습니다.

## 예시

데이터를 다운로드하고 인덱싱한 뒤에는 `3b. Chat.py`를 사용해 도메인 특화 질문에 자연어 답변을 받을 수 있습니다.

```text
Q: What pressure should the machine use to extract espresso?

A: 에스프레소 추출에는 머신 압력이 중요합니다. 일반적으로 9 bar 압력이 진하고 균형 잡힌 에스프레소를 만드는 데 적합합니다.
```

```text
Q: What is the controversy around Samsungs Space Zoom feature?

A: Samsung Space Zoom 기능이 실제 광학 성능보다 더 선명한 달 사진을 제공하는 것처럼 보일 수 있다는 점이 논란입니다.
```

## 시작하기

1. 의존성을 설치합니다.

    ```bash
    python -m venv venv
    ```

    Linux/Mac:

    ```bash
    source venv/bin/activate
    ```

    Windows:

    ```bash
    venv\Scripts\activate
    ```

    ```bash
    pip install -r requirements.txt
    ```

2. Marqo를 설치하고 실행합니다.

    [Marqo 시작 가이드](https://github.com/marqo-ai/marqo#Getting-started)를 참고하세요.

3. 사용 중인 플랫폼에 맞게 FFmpeg를 설치합니다.
4. [pyannote speaker-diarization용 Hugging Face API 키](https://huggingface.co/pyannote/speaker-diarization)를 받습니다.
5. [OpenAI API 키](https://platform.openai.com/account/api-keys)를 받습니다.
6. `.env_local`을 복사해 `.env`로 이름을 바꾸고 API 키를 넣습니다.

## 문제 해결

### `2. Process.py`의 심볼릭 링크 생성 오류

PyAnnote가 적절한 권한 없이 실행되면 이 오류가 발생할 수 있습니다. 관리자 또는 super user 권한으로 스크립트를 실행하세요.

## 사용법

데이터 다운로드:

```bash
python '1. Download.py'
```

데이터 처리 및 인덱싱:

```bash
python '2. Process.py'
```

이 단계는 GPU 사용을 강력히 권장합니다.

데이터 검색:

```bash
python '3a. Search.py'
```

대화형 Q&A:

```bash
python '3b. chat.py'
```
