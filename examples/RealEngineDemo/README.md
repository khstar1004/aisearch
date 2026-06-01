# 실제 Marqo 엔진 데모

이 데모는 `http://localhost:8882`에서 실행 중인 실제 Marqo API와 통신합니다.

## 저장소 기반 random-model 스택 시작

저장소 루트에서 실행합니다.

```powershell
docker compose -f examples\RealEngineDemo\compose-random-demo.yaml up --build
```

브라우저에서 엽니다.

```text
http://localhost:8100
```

`8100` 포트가 이미 사용 중이라면 다음처럼 포트를 바꿉니다.

```powershell
$env:MARQO_DEMO_UI_PORT=8101
docker compose -f examples\RealEngineDemo\compose-random-demo.yaml up --build
```

그다음 `http://localhost:8101`을 엽니다.

먼저 **Health**를 누른 뒤 **Create + Index**를 실행합니다. UI는 작은 same-origin 프록시를 통해 실제 Marqo API를 호출하므로, 라이브 엔진을 사용하면서도 브라우저 CORS 문제를 피합니다.

다른 PowerShell 창에서 다음 명령을 실행합니다.

```powershell
python examples\RealEngineDemo\real_marqo_demo.py --engine random --docs 2000
```

`random` 엔진은 실제 Marqo API, MIOC 경로, Vespa 스키마 배포, 문서 인덱싱, 벡터 저장, 검색 엔드포인트를 그대로 사용합니다. 큰 임베딩 모델을 다운로드하지 않으므로 인덱싱/검색 성능 감각을 빠르게 확인하기 좋습니다.

더 큰 실행은 다음처럼 진행합니다.

```powershell
python examples\RealEngineDemo\real_marqo_demo.py --engine random --docs 10000 --batch-size 200
```

## 중지

```powershell
docker compose -f examples\RealEngineDemo\compose-random-demo.yaml down
```
