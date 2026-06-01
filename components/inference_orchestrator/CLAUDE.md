# CLAUDE.md

이 파일은 이 저장소에서 Claude Code가 작업할 때 참고할 지침을 제공합니다.

## 프로젝트 개요

Marqo Inference Container는 Marqo tensor search engine의 ML 모델 추론을 처리하는 FastAPI 기반 서비스입니다. 주요 역할은 다음과 같습니다.

- HuggingFace, OpenCLIP 등 모델 로딩과 관리
- 이미지, 텍스트, 멀티모달 미디어 다운로드 및 전처리
- 성능 향상을 위한 추론 캐싱
- Triton Inference Server 연동
- OpenTelemetry 계측

## 개발 명령

### 환경 설정

- 의존성 관리는 `uv`를 사용합니다.
- Python 버전은 3.11 이상입니다.
- 테스트와 스크립트 실행 시 `PYTHONPATH=./src`를 설정합니다.
- 의존성 설치: `uv sync`
- 개발 의존성 설치: `uv sync --group dev`

## 프로젝트 구조

```text
src/inference_orchestrator/
├── api/                              # API 미들웨어와 OpenTelemetry 설정
├── core/                             # 핵심 설정과 로깅
├── errors/                           # 오류 정의와 기본 오류 클래스
├── schemas/                          # API 요청/응답 Pydantic 모델
├── services/
│   ├── inference_cache/              # 추론 결과 캐시 계층
│   ├── media_download_and_preprocess/ # 미디어 처리 파이프라인
│   ├── model_download/               # HuggingFace 모델 다운로드
│   └── triton_inference/             # Triton server 연동
├── config.py                         # 설정 관리
├── main.py                           # FastAPI 애플리케이션 진입점
├── on_start_script.py                # 시작 초기화 작업
└── version.py                        # 버전 정보
```

### 테스트

- 단위 테스트 실행: `PYTHONPATH=./src pytest tests/unit_tests/ -v`
- 테스트는 `tests/unit_tests/`에 있으며 소스 코드와 같은 패키지 계층을 따릅니다.
- 새 테스트를 추가하거나 기존 테스트를 변경했다면 반드시 실행해서 통과 여부를 확인합니다.
- 새 테스트를 만드는 것보다 기존 테스트 업데이트를 우선합니다.
- 관련 테스트 케이스와 공유 setup은 subtest로 묶습니다.

### 애플리케이션 실행

- 개발 실행: `PYTHONPATH=./src python -m inference_orchestrator.main`
- Docker 빌드: `docker build -t marqo-inference .`
- 환경 변수는 `.env` 파일 또는 셸 export로 설정할 수 있습니다.

## 아키텍처

### 핵심 구성

1. **API 계층** (`src/inference_orchestrator/`)
   - `main.py`: FastAPI 애플리케이션 진입점
   - `api/`: 미들웨어와 OpenTelemetry 설정
   - `schemas/`: 요청/응답 검증용 Pydantic 모델

2. **서비스 계층** (`src/inference_orchestrator/services/`)
   - `inference_cache/`: 모니터링을 포함한 LRU 캐시
   - `media_download_and_preprocess/`: 이미지/텍스트 다운로드와 전처리
   - `triton_inference/`: NVIDIA Triton Inference Server 연동
   - `model_download/`: HuggingFace Hub에서 모델 다운로드

3. **설정** (`src/inference_orchestrator/core/`)
   - `settings.py`: 환경 변수 지원 Pydantic settings
   - `config.py`: 설정 관리
   - 필요한 곳에 `@lru_cache()`를 사용해 싱글턴 패턴을 적용합니다.

### API 엔드포인트

주요 엔드포인트는 `/vectorise`입니다. `InferenceRequest`를 받아 임베딩을 반환합니다. 요청/응답은 JSON 또는 msgpack 경로를 사용할 수 있습니다.

### 오류 처리

- 모든 오류는 `errors/base_error.py`의 `BaseMarqoInferenceError`를 상속합니다.
- 세부 오류 타입은 `errors/inference_errors.py`와 `errors/common_errors.py`에서 정의합니다.

## 개발 지침

- 새 모듈은 기존 패키지 구조를 따릅니다.
- 단위 테스트는 소스 패키지 계층을 미러링해야 합니다.
- 적용 가능한 곳에서는 FastAPI `Depends()` 기반 의존성 주입을 사용합니다.
- 모든 설정은 환경 변수 기반이어야 합니다.
- 구조화 로깅과 적절한 로그 레벨을 사용합니다.
- 순환 import를 피해야 하는 경우를 제외하면 모든 import는 파일 상단에 둡니다.

### 테스트 작성 세부 지침

- 각 소스 패키지에는 대응하는 테스트 패키지를 둡니다.
- 공유 setup이 있는 관련 테스트는 subtest를 사용합니다.
- 단위 테스트에서는 Triton server, 모델 다운로드 같은 외부 의존성을 mock 처리합니다.
- 중요한 컴포넌트는 높은 테스트 커버리지를 유지합니다.
- 중복된 테스트 로직이 없는지 검토합니다.
- `assertEqual`을 사용할 때는 기대값을 첫 번째, 실제값을 두 번째 인자로 둡니다.
- subtest를 사용할 때는 메시지, 입력, 기대 출력으로 구성된 튜플 목록을 먼저 만들고 루프에서 `self.subTest`를 호출합니다.
- 가능하면 테스트 목적을 설명하는 docstring을 추가합니다.
- 기대값 검증은 구체적으로 작성합니다. 예를 들어 리스트 길이, 문자열 포함 여부, 값의 타입을 확인합니다.
- 환경 변수 관련 테스트는 루트의 `.env`가 결과에 영향을 줄 수 있으므로 테스트 안에서 필요한 값을 명시적으로 설정합니다.
- 꼭 필요하지 않다면 비공개 메서드는 테스트하지 않습니다.
- 단위 테스트에서는 `time.sleep` 같은 blocking call을 피하고 mock으로 지연이나 timeout을 시뮬레이션합니다.
- 통합 테스트는 모델 다운로드가 필요하고 오래 걸릴 수 있으므로 명시적으로 요청받지 않는 한 실행하지 않습니다.

## 중요 지침

- 요청받은 작업을 정확히 수행합니다.
- 꼭 필요하지 않다면 새 파일을 만들지 않습니다.
- 새 파일을 만드는 것보다 기존 파일 편집을 우선합니다.
- 명시적으로 요청받지 않았다면 문서 파일을 선제적으로 만들지 않습니다.
