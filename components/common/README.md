# marqo-common

Marqo 컴포넌트들이 모델 정의를 한 곳에서 공유하도록 만든 가벼운 공통 모듈입니다.

## 목적

이 패키지는 모델 레지스트리를 중앙화합니다. `marqo`와 `inference_orchestrator`가 모델 메타데이터(차원 수, Triton 설정 등)를 중복 없이 공유하도록 돕습니다.

## Docker 빌드

`marqo-common`에 의존하는 컴포넌트는 Docker 빌드 컨텍스트에 이 디렉터리를 포함해야 합니다. `components/` 디렉터리에서 빌드하세요.

```bash
docker build -f marqo/Dockerfile -t marqo .
docker build -f inference_orchestrator/Dockerfile -t inference-orchestrator .
```

Dockerfile은 `common/`을 이미지로 복사한 뒤 로컬 의존성으로 설치합니다.

## 모델 추가

`src/marqo_common/model_registry.py`의 `_MODEL_REGISTRY`에 항목을 추가합니다. S3 경로에는 `_MARQO_DEFAULT_MODELS_S3_BUCKET_PLACE_HOLDER`를 사용하세요. 이 값은 런타임에 실제 버킷 경로로 대체됩니다.

Python 3.11 이상이 필요하며 외부 의존성은 없습니다.
