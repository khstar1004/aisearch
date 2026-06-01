## 저장소 구성

- `components/marqo`: Marqo 핵심 API와 Vespa 기반 검색 엔진
- `components/inference_orchestrator`: 모델 추론, 전처리, 캐싱을 담당하는 FastAPI 서비스
- `components/model_management`: Triton 모델 다운로드, 로딩, 언로딩을 담당하는 서비스
- `components/common`: 여러 컴포넌트가 공유하는 모델 레지스트리
- `examples`: 이미지 검색, 실제 엔진 데모, 의류 검색, 음성 검색 등 사용 예제
- `perf_tests`: Locust 기반 성능 테스트

## 빠른 실행

로컬에서 기본 Marqo 컨테이너를 실행하려면 다음 명령을 사용합니다.

```bash
docker rm -f marqo
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:latest
```

예제별 실행 방법은 각 예제 디렉터리의 README를 참고하세요.
