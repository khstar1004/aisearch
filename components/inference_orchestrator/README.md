# Marqo Inference Orchestrator

Marqo tensor search engine의 ML 모델 추론을 담당하는 FastAPI 기반 서비스입니다. 모델 로딩/관리, 미디어 전처리, 추론 캐싱, NVIDIA Triton Inference Server 연동을 제공합니다.

## 주요 기능

- 모델 로딩과 관리: HuggingFace, OpenCLIP 등
- 이미지, 텍스트, 멀티모달 콘텐츠 다운로드 및 전처리
- 성능 향상을 위한 LRU/LFU 추론 캐시
- Triton Inference Server 연동
- 관측성을 위한 OpenTelemetry 계측
- 효율적인 통신을 위한 MessagePack 직렬화

## 요구 사항

- Python 3.11 이상
- 의존성 관리를 위한 [uv](https://github.com/astral-sh/uv)
- 프로덕션 배포 시 NVIDIA Triton Inference Server

## 설치

uv로 의존성을 설치합니다.

```bash
uv sync
```

테스트 도구를 포함한 개발 환경은 다음 명령을 사용합니다.

```bash
uv sync --group dev
```

## 사용법

### 서비스 실행

개발 모드:

```bash
PYTHONPATH=./src python -m inference_orchestrator.main
```

uvicorn 기반 프로덕션 모드:

```bash
PYTHONPATH=./src uvicorn inference_orchestrator.main:app --host 0.0.0.0 --port 8884
```

### Docker

컨테이너를 빌드합니다.

```bash
docker build -t marqo-inference .
```

컨테이너를 실행합니다.

```bash
docker run -p 8884:8884 marqo-inference
```

## API 엔드포인트

- `GET /`: 상태 확인과 기본 정보
- `POST /vectorise`: 콘텐츠에서 임베딩 생성. MessagePack을 받습니다.
- `GET /healthz`: 컨테이너 오케스트레이션용 liveness check
- `GET /models`: 로드된 모델 목록
- `DELETE /models?model_name={name}`: 메모리에서 모델 제거

## 프로젝트 구조

```text
inference_orchestrator/
├── src/inference_orchestrator/
│   ├── api/                               # API 미들웨어와 텔레메트리
│   ├── core/                              # 설정과 로깅
│   ├── errors/                            # 오류 정의
│   ├── schemas/                           # Pydantic 모델
│   ├── services/
│   │   ├── inference_cache/               # 추론 결과 캐시
│   │   ├── media_download_and_preprocess/ # 미디어 처리
│   │   └── triton_inference/              # Triton 연동과 추론 파이프라인
│   ├── config.py                          # 설정 관리
│   ├── main.py                            # FastAPI 진입점
│   ├── marqo_docs.py                      # 문서 유틸리티
│   ├── on_start_script.py                 # 시작 초기화
│   └── version.py                         # 버전 정보
├── tests/
│   ├── integration_tests/
│   └── unit_tests/
├── pyproject.toml
└── README.md
```

## 설정

설정은 환경 변수와 Pydantic settings로 관리합니다. 주요 환경 변수는 다음과 같습니다.

- `TRITON_SERVER_URL`: Triton Inference Server URL
- `CACHE_SIZE`: 캐시할 추론 결과의 최대 개수
- `CACHE_STRATEGY`: 캐시 제거 전략. LRU 또는 LFU
- `LOG_LEVEL`: 로깅 레벨. DEBUG, INFO, WARNING, ERROR
- `OTEL_ENABLED`: OpenTelemetry 계측 활성화 여부

사용 가능한 전체 설정은 `src/inference_orchestrator/core/settings.py`를 참고하세요.

## 테스트

단위 테스트:

```bash
PYTHONPATH=./src pytest tests/unit_tests/ -v
```

통합 테스트:

```bash
PYTHONPATH=./src pytest tests/integration_tests/ -v
```

특정 테스트 파일:

```bash
PYTHONPATH=./src pytest tests/unit_tests/services/inference_cache/test_cache.py -v
```

## 아키텍처

### 핵심 구성

1. **API 계층**: FastAPI 애플리케이션, OpenTelemetry, MessagePack 직렬화, 예외 핸들러와 미들웨어를 담당합니다.
2. **서비스 계층**: 추론 캐시, 이미지/텍스트 전처리, Triton 추론 요청을 담당합니다.
3. **모델 관리**: 임베딩 모델 인터페이스, 전처리/추론/후처리 파이프라인, 모델 로딩과 언로딩을 관리합니다.
4. **설정 계층**: 환경 변수 기반 Pydantic settings, JSON 구조화 로깅, `@lru_cache()` 기반 싱글턴 패턴을 사용합니다.

### 요청 흐름

1. 클라이언트가 MessagePack으로 인코딩한 추론 요청을 `/vectorise`로 보냅니다.
2. Pydantic 스키마가 요청을 검증합니다.
3. 기존 결과가 있는지 캐시를 확인합니다.
4. 캐시 미스라면 미디어를 다운로드/전처리하고, 적절한 추론 파이프라인을 선택하며, 필요 시 모델을 로드한 뒤 Triton에서 추론을 실행합니다.
5. 결과를 캐시에 저장하고 MessagePack 응답을 반환합니다.

## 오류 처리

모든 오류는 `errors/base_error.py`의 `BaseMarqoInferenceError`를 상속합니다. 서비스 계층 오류는 `services/errors.py`에 정의합니다.

- `ServiceError`: 서비스 오류의 기본 클래스
- `InternalServerError`: 내부 서버 오류(500)
- 모델별 오류는 각 모델 구현 파일에 정의합니다.
