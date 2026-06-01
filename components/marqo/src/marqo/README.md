# 개발자 가이드

Marqo에 기여해 주셔서 감사합니다. 오픈소스 커뮤니티의 기여는 Marqo를 더 실용적인 tensor search engine으로 만드는 데 도움이 됩니다.

단위 테스트 실행 방법은 저장소 루트의 기여 가이드를 참고하세요. 이 저장소에서는 기존 히스토리와 함께 오래된 기여 문서를 정리했으므로, 현재 기준은 이 문서와 루트 `CLAUDE.md`를 우선합니다.

## Docker 밖에서 Marqo 로컬 실행

개발 중 Docker 밖에서 Marqo를 실행하는 방법은 두 가지입니다.

- 옵션 A: IDE에서 직접 실행
- 옵션 B: `uvicorn`으로 실행

두 방법 모두 Marqo 실행 전에 로컬 Vespa 설정이 필요합니다.

### 준비

1. Marqo 저장소와 `marqo-base` 저장소를 준비합니다.

```bash
git clone https://github.com/marqo-ai/marqo.git
git clone https://github.com/marqo-ai/marqo-base.git
```

2. 환경에 맞는 Marqo 의존성을 설치합니다.

AMD 머신:

```bash
pip install -r marqo-base/requirements/amd64-gpu-requirements.txt
```

ARM 머신:

```bash
pip install -r marqo-base/requirements/arm64-requirements.txt
```

테스트 실행이 필요하다면 개발 의존성도 설치합니다.

```bash
pip install -r marqo/requirements.dev.txt
```

3. Vespa Docker 이미지를 실행합니다.

```bash
docker run --detach --name vespa --hostname vespa-tutorial \
  --publish 8080:8080 --publish 19071:19071 \
  vespaengine/vespa:latest
```

4. `scripts/vespa_local` 애플리케이션 패키지로 Vespa를 설정합니다.

```bash
(cd scripts/vespa_local && zip -r - * | curl --header "Content-Type:application/zip" --data-binary @- http://localhost:19071/application/v2/tenant/default/prepareandactivate)
```

브라우저에서 `http://localhost:8080`을 열어 Vespa가 올바르게 설정되었는지 확인할 수 있습니다.

5. 커스텀 searcher JAR 빌드를 위해 JDK와 Maven을 설치합니다.

JDK 설치 후 `JAVA_HOME`과 `PATH`를 설정하고 다음 명령으로 확인합니다.

```bash
java -version
```

Maven 설치 후 `PATH`를 설정하고 다음 명령으로 확인합니다.

```bash
mvn -version
```

6. 로컬 Marqo 저장소의 `vespa` 디렉터리에서 JAR 파일을 빌드합니다.

```bash
mvn clean package
```

빌드 후 `vespa/target` 폴더에 `marqo-custom-searchers-deploy.jar`가 생성됩니다. 이 파일은 커스텀 searcher를 Vespa에 배포할 때 사용합니다.

### 옵션 A. IDE에서 Marqo 실행

1. IDE에서 Marqo 프로젝트를 열고 `src/marqo/tensor_search/api.py`로 이동합니다.
2. 다음 환경 변수로 `api.py` 실행/debug 설정을 만듭니다.

```text
MARQO_ENABLE_BATCH_APIS=true
MARQO_LOG_LEVEL=debug
MARQO_MODELS_TO_PRELOAD=[]
VESPA_CONFIG_URL=http://localhost:19071
VESPA_DOCUMENT_URL=http://localhost:8080
VESPA_QUERY_URL=http://localhost:8080
```

3. IDE에서 파일을 직접 실행해 Marqo를 시작합니다.
4. 필요한 위치에 breakpoint를 설정해 디버깅합니다.

### 옵션 B. `uvicorn`으로 Marqo 실행

위 준비를 마친 뒤 다음 명령을 실행합니다.

```bash
export MARQO_ENABLE_BATCH_APIS=true
export MARQO_LOG_LEVEL=debug
export VESPA_CONFIG_URL=http://localhost:19071
export VESPA_DOCUMENT_URL=http://localhost:8080
export VESPA_QUERY_URL=http://localhost:8080
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"
cd src/marqo/tensor_search
uvicorn api:app --host 0.0.0.0 --port 8882 --reload
```

## Redis 설정

Marqo는 동시성 throttling을 위해 Redis를 사용합니다. Docker로 Marqo를 실행하면 Redis가 자동으로 설정되지만, Docker 밖에서 로컬 실행하는 경우 Redis를 직접 준비해야 throttling을 사용할 수 있습니다.

Redis가 없어도 Marqo는 실행되지만 throttling은 비활성화되고 관련 경고가 표시됩니다. 경고를 숨기려면 throttling을 완전히 비활성화합니다.

```bash
export MARQO_ENABLE_THROTTLING='FALSE'
```

Ubuntu 22.04 기준 Redis 7.0.8 설치 예시는 다음과 같습니다.

```bash
apt-get update
apt-get install redis-server -y
```

더 최신 Redis가 필요하다면 Redis 공식 패키지 저장소를 설정한 뒤 설치합니다.

```bash
apt install lsb-release
curl -fsSL https://packages.redis.io/gpg | gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/redis.list
apt-get update
apt-get install redis-server -y
```

Redis 실행:

```bash
redis-server /etc/redis/redis.conf
```

## Docker로 Marqo 개발 실행

### 옵션 C. Docker 컨테이너로 빌드 및 실행

1. Marqo 루트 디렉터리로 이동합니다.
2. 다음 명령을 실행합니다.

```bash
docker rm -f marqo &&
     DOCKER_BUILDKIT=1 docker build . -t marqo_docker_0
     docker run --name marqo -p 8882:8882 marqo_docker_0
```

## GPU 사용

Docker 내부와 외부에서 GPU를 사용하는 방법은 다릅니다.

### Docker 밖에서 GPU 사용

Docker 밖의 Marqo는 시스템의 PyTorch/GPU 설정을 그대로 사용합니다. PyTorch에서 GPU가 정상적으로 보인다면 Marqo에서도 사용할 수 있습니다. 단, PyTorch CUDA 버전과 GPU 드라이버 버전이 맞아야 합니다.

### Docker 안에서 GPU 사용

현재는 CUDA 기반 NVIDIA GPU만 지원합니다.

1. `docker run` 명령에 `--gpus all` 플래그를 추가합니다.

```bash
docker rm -f marqo &&
     DOCKER_BUILDKIT=1 docker build . -t marqo_docker_0 &&
     docker run --name marqo --gpus all -p 8882:8882 marqo_docker_0
```

2. Docker에서 GPU를 사용하려면 [nvidia-docker2](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)를 설치합니다.

Ubuntu 기반 머신 예시:

```bash
distribution=$(. /etc/os-release;echo $ID$VERSION_ID) \
      && curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
      && curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
            sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
            sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-docker2
```

## AWS EC2에서 Marqo 사용

프로덕션 용도로는 권장하지 않습니다.

1. [Docker 공식 문서](https://docs.docker.com/engine/install/ubuntu/)를 참고해 Docker를 설치합니다.
2. SSH timeout을 줄이기 위해 `nano ~/.ssh/config`로 설정 파일을 열고 `ServerAliveInterval 50`을 추가합니다.
3. Marqo를 실행합니다.

```bash
docker run --name marqo -p 8882:8882 marqoai/marqo:latest
```

## 문제 해결

### 드라이버

Marqo 안에서 GPU를 사용하려면 호스트에 NVIDIA 드라이버가 설치되어 있어야 합니다. 다음 명령으로 현재 드라이버 상태를 확인합니다.

```bash
nvidia-smi
```

출력이 없다면 GPU 설정에 문제가 있을 수 있으며 드라이버 설치 또는 업데이트가 필요합니다.

### CUDA

드라이버뿐 아니라 CUDA 버전도 맞아야 합니다. Marqo Dockerfile은 기본적으로 CUDA 11.4.2를 사용하도록 설정되어 있습니다. 다른 CUDA 버전이 필요하다면 Dockerfile을 수정할 수 있습니다.

### GPU와 CUDA 상태 확인

Python에서 PyTorch GPU 상태를 확인합니다.

```python
import torch
torch.cuda.is_available()
torch.version.cuda
torch.cuda.device_count()
```

드라이버와 지원 가능한 최대 CUDA 버전은 다음 명령으로 확인합니다.

```bash
nvidia-smi
```

PyTorch는 자체 bundled CUDA를 포함하므로 여러 CUDA 버전을 사용할 수 있습니다. 설치 방법은 [PyTorch 시작 문서](https://pytorch.org/get-started/locally/)를 참고하세요.

## `openapi.json` 추출

Marqo가 로컬에서 실행 중일 때 JSON만 가져오려면 다음 명령을 사용합니다.

```bash
curl http://localhost:8882/openapi.json
```

사람이 읽기 좋은 Swagger 문서는 `http://localhost:8882/docs`에서 확인합니다.

## IDE 팁

### PyCharm

이 프로젝트는 Pydantic dataclass를 사용합니다. 기본 PyCharm은 이 dataclass 초기화를 잘 파싱하지 못할 수 있습니다. [Pydantic 플러그인](https://plugins.jetbrains.com/plugin/12861-pydantic)을 사용하면 도움이 됩니다.
