# CLAUDE.md

이 파일은 이 저장소에서 Claude Code가 작업할 때 참고할 지침을 제공합니다.

## 프로젝트 개요

Marqo Model Management Container는 Triton Inference Server용 머신러닝 모델을 관리하는 FastAPI 기반 서비스입니다. 모델 다운로드, 로딩, 언로딩 작업을 담당합니다.

## 개발 명령

### 환경 설정

- 의존성 관리는 `uv`를 사용합니다.
- 테스트와 스크립트 실행 시 `PYTHONPATH=./src`를 설정합니다.
- 의존성 설치: `uv sync`
- 개발 의존성 설치: `uv sync --group dev`

### 테스트

- 단위 테스트 실행: `PYTHONPATH=./src uv run pytest tests/unit_tests/ -v`
- 테스트는 `tests/unit_tests/`에 있으며 소스 코드와 같은 패키지 계층을 따릅니다.
- 공유 setup이 있는 관련 테스트는 subtest를 사용합니다.

### 애플리케이션 실행

- 개발 실행: `PYTHONPATH=./src python -m model_management.main`
- Docker 빌드: `docker build -t marqo-model-management .`
- 애플리케이션은 기본적으로 8883 포트에서 실행됩니다.

## 아키텍처

### 핵심 구성

1. **API 계층** (`src/model_management/api/`)
   - `main.py`: FastAPI 애플리케이션 진입점
   - `v1_routes.py`: 모델 작업용 API 엔드포인트
   - `lifespan.py`: 애플리케이션 시작/종료 핸들러
   - `exception_handlers.py`: 전역 오류 처리
   - `request_id.py`: 요청 추적 미들웨어

2. **서비스 계층** (`src/model_management/service/`)
   - `model_manager/`: 모델 다운로드와 Triton config 생성
   - `triton/`: Triton Inference Server와 통신하는 클라이언트

3. **설정** (`src/model_management/core/`)
   - `settings.py`: 환경 변수 지원 Pydantic settings
   - `config.py`: 의존성 주입 설정
   - 싱글턴 패턴에는 `@lru_cache()`를 사용합니다.

### 주요 서비스

- **ModelManager**: 모델을 다운로드하고 Jinja2 템플릿으로 Triton `config.pbtxt` 파일을 생성합니다.
- **TritonClient**: Triton Inference Server API(`/v2/repository/models/`)용 HTTP 클라이언트입니다.
- **TritonModelDownloader**: 여러 소스에서 모델 파일 다운로드를 처리합니다.

### 설정

환경 변수는 `env.example`을 참고합니다.

- `TRITON_URL`: Triton server 엔드포인트. 필수입니다.
- `MODEL_BASE_DIR`: 로컬 모델 저장 경로. 기본값은 `./cache/models`입니다.
- `MARQO_MODELS_TO_PRELOAD`: 시작 시 로드할 모델의 JSON 배열입니다.
- `LOG_LEVEL`: 로그 레벨. debug, info, warning, error
- `LOG_FORMAT`: 로그 포맷. text 또는 json

### API 엔드포인트

- `POST /v1/models/load`: 모델을 Triton에 로드합니다.
- `POST /v1/models/{model_name}/unload`: Triton에서 모델을 언로드합니다.

### 스키마 설계

- 요청/응답 검증에는 Pydantic 모델을 사용합니다.
- `TritonModelProperties`: Triton용 모델 설정을 정의합니다.
- `LoadModelRequest`: API 요청 wrapper입니다.
- 구조화된 problem response를 위한 커스텀 오류 처리를 사용합니다.

### 의존성

- **FastAPI**: 웹 프레임워크
- **Pydantic**: 데이터 검증과 settings
- **httpx**: Triton 통신용 HTTP 클라이언트
- **Jinja2**: Triton config 생성용 템플릿 엔진
- **fsspec/s3fs**: 모델 다운로드용 파일 시스템 추상화

## 개발 지침

- 새 모듈은 기존 패키지 구조를 따릅니다.
- 단위 테스트는 소스 패키지 계층을 미러링해야 합니다.
- FastAPI `Depends()` 기반 의존성 주입 패턴을 사용합니다.
- 모든 설정은 환경 변수 기반이어야 합니다.
- 요청 ID를 포함한 구조화 로깅을 사용합니다.
- 모델은 `MODEL_BASE_DIR`에 다운로드하고 모델 이름별로 정리합니다.
- 소스 코드와 테스트 파일 모두에서 가능하면 모든 import는 파일 상단에 둡니다. 순환 import를 피해야 할 때만 inline import를 사용합니다.

### 테스트 작성 세부 지침

- 각 소스 패키지에는 대응하는 테스트 패키지를 둡니다.
- 공유 setup이 있는 관련 테스트는 subtest를 사용합니다.
- 단위 테스트에서는 Triton server 같은 외부 의존성을 mock 처리합니다.
- 중요한 컴포넌트는 높은 테스트 커버리지를 유지합니다.
- 중복된 테스트 로직이 없는지 검토합니다.
- `assertEqual`을 사용할 때는 기대값을 첫 번째, 실제값을 두 번째 인자로 둡니다.
- subtest를 사용할 때는 메시지, 입력, 기대 출력으로 구성된 튜플 목록을 먼저 만들고 루프에서 `self.subTest`를 호출합니다.
- 가능하면 테스트 목적을 설명하는 docstring을 추가합니다.
- 기대값 검증은 구체적으로 작성합니다. 예를 들어 리스트 길이, 문자열 포함 여부, 값의 타입을 확인합니다.
- 환경 변수 관련 테스트는 루트의 `.env`가 결과에 영향을 줄 수 있으므로 테스트 안에서 필요한 값을 명시적으로 설정합니다.
- 꼭 필요하지 않다면 비공개 메서드는 테스트하지 않습니다.
- 단위 테스트에서는 `time.sleep` 같은 blocking call을 피하고 mock으로 지연이나 timeout을 시뮬레이션합니다.
- 중복을 줄이기 위해 helper method를 사용합니다.
- 관련 assertion은 하나의 테스트로 묶습니다.
- 여러 설정 조합은 subtest로 검증합니다.
- 테스트 이름은 무엇을 검증하는지 명확하고 간결해야 합니다.
- 중복 테스트 로직을 두지 않습니다.
