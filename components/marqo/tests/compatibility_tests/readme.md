로컬 머신에서 하위 호환성 테스트를 실행하는 절차입니다.

1. 처음 실행한다면 가상 환경을 만들고 필요한 패키지를 설치합니다.
2. 가상 환경을 활성화합니다.
3. Docker 데몬이 실행 중인지 확인합니다.
4. AWS 자격 증명을 설정합니다.
5. Marqo 저장소를 Python 경로에 추가합니다. 예: `cd marqo` 후 `export PYTHONPATH="$PWD/src:$PWD:$PYTHONPATH"`
6. 다음 명령으로 테스트를 실행합니다.

```bash
python3 tests/compatibility_tests/compatibility_test_runner.py \
  --mode "backwards_compatibility" \
  --from_version "2.10.2" \
  --to_version "2.14.1" \
  --to_image "424082663841.dkr.ecr.us-east-1.amazonaws.com/marqo-compatibility-tests@sha256:c1c596f900e10b48e1ea6ff66e22f4d2da3d5b684fc08b02e3ad11baa21f9294"
```

7. 롤백 테스트는 mode를 `rollback`으로 전달합니다.
8. 이곳의 모든 폴더는 API 폴더이며, 테스트는 대상 API에 해당하는 폴더 안에 작성됩니다.
9. 개발 중 API 폴더 안에 하위 폴더를 만들면 해당 하위 폴더에 빈 `__init__.py`를 추가해 패키지로 선언하세요.
10. 테스트를 작성할 때는 동시에 실행되는 다른 테스트와 충돌하지 않도록 고유한 이름의 인덱스를 생성합니다. 이름은 settings dict에 정의합니다.
11. 모든 테스트 케이스는 새 `.py` 파일에 작성합니다.
12. 테스트에서 사용하는 모든 인덱스는 정리될 수 있도록 `cls.indexes_to_test_on`에 포함해야 합니다.
