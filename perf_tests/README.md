# Marqo 성능 테스트

Marqo 성능 테스트는 [Locust](https://docs.locust.io/en/stable/what-is-locust.html)를 사용합니다.

## 로컬 실행 설정

### 준비

```shell
# src 폴더와 의존성이 충돌하지 않도록 새 가상 환경을 만듭니다.
python -m venv ./venv_perf_tests
source ./venv-perf-tests/bin/activate

cd perf_tests
pip install -r requirements.txt
```

### Marqo 시작

컨테이너 또는 로컬 IDE에서 8882 포트로 Marqo 서버를 시작합니다.

```shell
docker run --name marqo -d -p 8882:8882 -e MARQO_MODELS_TO_PRELOAD='["hf/e5-base-v2"]' marqoai/marqo
```

### 로컬 성능 테스트 실행

```shell
# locust.conf의 기본 설정을 사용합니다.
locust

# CLI 파라미터로 기본 설정을 덮어쓸 수 있습니다.
locust -u <user_count> -r <spawn-rate> -t <duration> -H <host> -f <test_file>

# 로컬 실행 시 기본적으로 hf/e5-base-v2 모델을 사용하는 locust-test 인덱스를 만듭니다.
# 환경 변수로 인덱스 이름이나 모델 이름을 지정할 수 있습니다.
MARQO_INDEX_NAME=<index_name> MARQO_INDEX_MODEL_NAME=<model_name> locust

# 호스트와 API 키를 제공하면 Marqo Cloud 인스턴스를 대상으로 실행할 수도 있습니다.
MARQO_INDEX_NAME=<index_name> MARQO_CLOUD_API_KEY=<your api key> locust -H <host>

# 실행 후 report/report.html에 테스트 보고서가 생성됩니다.
```

## GitHub에서 실행

GitHub Action이 master에 병합된 뒤 내용을 추가합니다.

## 새 테스트 케이스 개발

Locust 테스트 케이스는 일반 Python 코드로 작성합니다. 새 테스트 시나리오는 별도의 Locust Python 파일로 추가할 수 있습니다. 현재 프로젝트 구조는 다음과 같습니다.

```text
| - common/       # 모든 테스트 케이스가 재사용하는 유틸리티
| - locustfiles/  # 재사용 가능한 테스트 케이스(TaskSet)
| - test_suite_1.py
\ - test_suite_2.py
```

`random_index_and_tensor_search.py`를 예시로 참고하세요. 더 많은 예시는 [Locust 문서](https://docs.locust.io/en/stable/writing-a-locustfile.html)에서 확인할 수 있습니다.

### 로컬 IDE 설정

PyCharm을 사용한다면 다음을 설정합니다.

- Settings -> Project -> Python Interpreter에서 새 가상 환경의 Python 인터프리터 선택
- Settings -> Project -> Project Structure에서 `src` 폴더의 Sources 표시 해제
- Settings -> Python Debugger에서 `Gevent compatible` 옵션 체크

```python
# SearchUser 테스트 케이스를 로컬에서 디버그하는 예시입니다.
from locust import run_single_user

if __name__ == "__main__":
    run_single_user(SearchUser)
```

### 새 의존성 추가

`requirements.txt`는 pip-tools로 `requirements.in`에서 생성합니다. `requirements.txt`를 직접 수정하지 말고, 직접 의존성을 `requirements.in`에 추가한 뒤 다음 명령을 실행하세요.

```shell
pip install pip-tools
pip-compile requirements.in
```
