# Marqo에서 텍스트-이미지 검색을 5줄로 구현하기

*이 문서는 [Marqo](https://www.marqo.ai/)로 텍스트를 사용해 이미지를 검색하는 방법을 단계별로 설명합니다.*

<p align="center">
<img src="asset/example.png">
</p>

[Marqo](https://www.marqo.ai/)는 멀티모달 검색을 지원하는 tensor 기반 검색 엔진입니다. 이 예제에서는 몇 장의 이미지를 인덱싱하고, 자연어 문장으로 해당 이미지를 찾는 텍스트-이미지 검색 엔진을 구성합니다. 전체 코드는 [노트북](imagesearchguide.ipynb)에 있습니다.

## 설정

### Marqo 설치

이 예제는 [COCO dataset](https://cocodataset.org/#home)에서 고른 5개 이미지를 사용합니다.

<p align="center">
  <img src="data/image2.jpg" width="150" />
  <img src="data/image1.jpg" width="150" />
  <img src="data/image0.jpg" width="110" />
  <img src="data/image3.jpg" width="100" />
  <img src="data/image4.jpg" width="150" />
</p>

먼저 Docker로 Marqo를 실행합니다.

```bash
docker rm -f marqo
docker pull marqoai/marqo:2.0.0
docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:2.0.0
```

그다음 새 환경을 만들고 Marqo 클라이언트를 설치합니다.

```bash
conda create -n marqo-client python=3.8
conda activate marqo-client

pip install marqo matplotlib
```

Python에서 설치를 확인합니다.

```python
import marqo
mq = marqo.Client("http://localhost:8882")
```

이 문서는 Marqo 2.0.0을 기준으로 작성되었습니다.

### 이미지 다운로드

예제 이미지는 [GitHub의 data 디렉터리](./data)에 있습니다. 디렉터리 구조는 다음과 같아야 합니다.

<p align="center">
 <img src="./asset/directory_diagram.png"/>
</p>

## Marqo로 검색하기

### 인덱스 생성

이미지 검색을 위해 OpenCLIP 계열 모델을 사용하고 URL/포인터를 이미지로 처리하도록 설정합니다.

```python
index_name = "image-search-guide"

settings = {
    "model": "open_clip/ViT-B-32/laion2b_s34b_b79k",
    "treatUrlsAndPointersAsImages": True,
}

mq.create_index(index_name, settings_dict=settings)
```

멀티모달 검색을 사용하려면 `"treatUrlsAndPointersAsImages": True` 설정이 필요합니다. 모델은 CLIP 계열 모델을 선택합니다.

### 로컬 이미지 접근

Marqo는 Docker 안에서 실행되므로 로컬 이미지 파일에 직접 접근할 수 없습니다. 간단한 해결책은 로컬 이미지 폴더를 HTTP 서버로 노출하는 것입니다.

```python
import subprocess

local_dir = "./data/"
pid = subprocess.Popen(
    ["python3", "-m", "http.server", "8222", "--directory", local_dir],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.STDOUT,
)
```

이제 Docker 컨테이너에서 접근할 수 있는 이미지 URL을 만듭니다.

```python
import glob
import os

locators = glob.glob(local_dir + "*.jpg")
docker_path = "http://host.docker.internal:8222/"
image_docker = [docker_path + os.path.basename(f) for f in locators]

print(image_docker)
```

### 이미지 인덱싱

Marqo의 입력 문서는 dictionary의 list입니다.

```python
documents = [{"image_docker": image, "_id": str(idx)} for idx, image in enumerate(image_docker)]
```

이 문서를 인덱스에 추가합니다.

```python
mq.index(index_name).add_documents(
    documents,
    tensor_fields=["image_docker"],
    device="cpu",
    client_batch_size=1,
)
```

CUDA GPU를 사용할 수 있다면 `device="cuda"`로 바꿔 인덱싱을 더 빠르게 할 수 있습니다.

### 검색

자연어로 찾고 싶은 이미지를 설명합니다.

```python
search_results = mq.index(index_name).search(
    "A rider on a horse jumping over the barrier",
    limit=1,
    device="cpu",
)
```

결과는 Marqo hit 형식으로 반환됩니다.

```python
print(search_results)
```

이미지를 직접 확인하려면 반환된 URL을 로컬 경로로 바꿔 표시합니다.

```python
import requests
from PIL import Image

fig_path = search_results["hits"][0]["image_docker"].replace(docker_path, local_dir)
display(Image.open(fig_path))
```

<p align="center">
    <img src="./asset/result.png">
</p>

## 정리

Marqo로 텍스트-이미지, 이미지-텍스트, 이미지-이미지 같은 멀티모달 검색을 구현하는 기본 흐름은 단순합니다.

1. 환경 설정: `conda`, `pip`
2. 인덱스 생성: `create_index()`
3. 문서 추가: `add_documents()`
4. 검색: `index().search()`
