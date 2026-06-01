# 일반 지침

- 가능하면 모든 import는 파일 상단에 둡니다.
- `structured_vespa_index`는 deprecated 상태이므로, 상속 구조상 연결되어 보이더라도 변경은 `semi_structured_vespa_index`에 직접 적용합니다.

# 환경 설정

명령을 실행하기 전에 가상 환경이 활성화되어 있는지 확인합니다. `.env`의 환경 변수와 `.venv` 가상 환경을 사용합니다.

검색기(`HybridSearcher.java`)를 변경했다면 다시 시도하기 전에 `mvn clean package`로 빌드하고 Vespa에 애플리케이션 패키지를 재배포해야 합니다.

# 테스트

- 단위 테스트는 `./tests/unit_tests`, 통합 테스트는 `./tests/integ_tests`, API 테스트는 `./tests/api_tests/v1/tests/api_tests`에 있습니다.
- 단위/통합 테스트를 실행할 때는 저장소 루트를 작업 디렉터리로 두고 `PYTHONPATH=./src`를 설정합니다.
- 통합 테스트나 API 테스트를 실행한다면 먼저 `docker ps`로 Vespa가 실행 중인지 확인합니다. 실행 중이 아니면 `python scripts/vespa_local/vespa_local.py full_start`로 Vespa를 시작합니다.
- API 테스트를 실행하려면 먼저 별도 프로세스에서 `PYTHONPATH=./src MARQO_ENABLE_BATCH_APIS=true MARQO_MODE=COMBINED` 환경으로 `src/marqo/tensor_search/api.py`를 실행합니다. API가 실행 중인 동안 `PYTHONPATH=./tests/api_tests/v1/tests/api_tests`로 pytest를 실행합니다. Marqo API가 실행되지 않으면 중단합니다. 테스트가 끝나면 Marqo API 프로세스를 종료합니다.
- 단위 테스트는 테스트 대상 코드와 같은 패키지 계층을 따라야 합니다.
- 새 테스트를 추가하거나 기존 테스트를 변경했다면 반드시 실행해서 통과 여부를 확인합니다.
- 기존 테스트가 있다면 새 테스트를 만들기보다 해당 테스트를 업데이트하는 방식을 우선합니다.
- 동일한 setup을 공유하는 테스트는 가능한 경우 subtest로 묶습니다.

# 핵심 컴포넌트

- **Tensor Search Engine**: `src/marqo/tensor_search/` - 주요 검색 구현
- **Inference Engine**: `src/marqo/core/inference/` - ML 모델 추론과 모달리티 감지
- **Vespa Integration**: `src/marqo/vespa/` - 벡터 데이터베이스 클라이언트
- **API Layer**: `src/marqo/tensor_search/api.py` - FastAPI HTTP 엔드포인트

# 인덱스 유형

1. **Unstructured**: 유연한 스키마와 자동 필드 감지를 제공하는 레거시 인덱스 유형입니다. 보통 unstructured index를 언급할 때는 이를 대체한 semi-structured index를 의미합니다. 사용자는 이 유형의 새 인덱스를 만들 수 없습니다.
2. **Structured**: 엄격한 필드 타입을 갖는 사전 정의 스키마입니다.
3. **Semi-structured**: 선택적 스키마 정의를 갖는 하이브리드 방식입니다.

# 검색 방식

- **TENSOR**: ML 임베딩을 사용하는 의미/벡터 검색
- **LEXICAL**: 전통적인 키워드 기반 검색
- **HYBRID**: 랭킹 퓨전(RRF, Reciprocal Rank Fusion)을 결합한 검색

# Vespa 인덱스 관리

각 인덱스 유형에는 전용 핸들러가 있습니다.

- `src/marqo/core/unstructured_vespa_index/`
- `src/marqo/core/structured_vespa_index/`
- `src/marqo/core/semi_structured_vespa_index/`

# 브랜치 구조

- **메인 브랜치**: `mainline`
- **기능 브랜치**: 보통 `username/feature-description`

# 오류 처리

코어 클래스는 `marqo.core.exceptions` 또는 `marqo.exceptions`만 발생시켜야 하며, `marqo.api.exceptions`를 직접 발생시키면 안 됩니다. API 예외로의 매핑은 API 계층에서 처리합니다.
