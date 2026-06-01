# 로컬 Vespa 설정

Marqo나 단위 테스트 스위트를 로컬에서 실행하려면 Vespa 노드 또는 클러스터가 실행 중이어야 합니다. 이 디렉터리는 단일 노드(컨테이너 1개) 또는 multinode-HA Vespa를 로컬 머신에 설정하는 스크립트를 제공합니다.

### Vespa 버전 설정

- 기본적으로 이 스크립트는 `vespa_local.py`에 정의된 Vespa `8.431.32`를 사용합니다. 변경하려면 원하는 버전으로 `VESPA_VERSION` 변수를 설정합니다.

```commandline
export VESPA_VERSION="latest"
```

### Vespa 최대 디스크 사용률 설정

**주의:** 프로덕션 사용을 권장하지 않습니다. 로컬 개발 전용입니다.

- 기본 Vespa 디스크 사용률 제한은 0.75(75%)입니다. 변경하려면 `VESPA_DISK_USAGE_LIMIT`를 0과 1 사이의 float 값으로 설정합니다.

```commandline
export VESPA_DISK_USAGE_LIMIT=0.9
```

## 단일 노드 Vespa(기본 및 권장)

- 머신에서 Vespa 컨테이너 1개를 실행합니다. 이 컨테이너가 config, API, content 노드 역할을 모두 맡습니다.
- 이는 replica 0개, shard 1개인 Vespa 실행과 같습니다.
- 다음 명령으로 시작합니다.

```commandline
python vespa_local.py start
```

이 명령은 Vespa Docker 컨테이너를 실행한 뒤 `singlenode/` 디렉터리의 `services.xml` 파일을 이 디렉터리로 복사합니다. 해당 파일은 배포 시 Vespa 애플리케이션에 포함됩니다.

## 멀티 노드 Vespa

- 다음 노드로 Vespa 클러스터를 실행합니다.
- config 노드 3개
- `m`개의 content 노드: `m = number_of_shards * (1 + number_of_replicas)`
- `n`개의 API 노드: `n = max(2, number_of_content_nodes)`

예를 들어 shard 2개, replica 1개라면 content 노드 4개와 API 노드 2개를 실행합니다.

```commandline
python vespa_local.py start --Shards 2 --Replicas 1
```

## 배포

Vespa 노드를 시작한 뒤 이 디렉터리의 파일로 Vespa 애플리케이션을 배포합니다.

```commandline
python vespa_local.py deploy-config
```

단일 노드에서는 다음 명령으로 준비 상태를 확인할 수 있습니다.

```commandline
curl -s http://localhost:19071/state/v1/health
```

멀티 노드에서는 시작 스크립트가 API/content 노드에 해당하는 URL 목록을 출력합니다. 각 URL에 curl을 실행해 준비 상태를 확인하세요.

## 기타 명령

### Vespa 중지

```commandline
python vespa_local.py stop
```

### Vespa 재시작

```commandline
python vespa_local.py restart
```

## 참고

- 이 스크립트에서 stop, restart 같은 명령을 실행하면 `vespa`라는 컨테이너가 있는지 확인합니다. 있으면 단일 노드 구성으로, 없으면 멀티 노드 구성으로 간주합니다.
- 멀티 노드에서 config/API 노드는 각각 약 1GB 메모리를 사용하고, content 노드는 각각 약 500MB를 사용한다고 예상하세요. 리소스 할당을 그에 맞게 조정합니다.
