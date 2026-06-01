# Marqo Streamlit 데모 애플리케이션

## 사전 준비

다음 소프트웨어가 필요합니다.

```text
Python 3.8
```

## 시작하기

1. [Clothing Dataset](https://github.com/alexeygrigorev/clothing-dataset)을 다운로드해 `streamlit_marqo_demo.py` 스크립트가 있는 디렉터리에 둡니다.

2. 스크립트 디렉터리에서 다음 명령을 실행해 HTTP 서버를 시작합니다.

    ```bash
    python3 -m http.server 8222
    ```

    이 서버는 Marqo Docker 컨테이너가 로컬 OS의 파일을 읽을 수 있게 해 줍니다. 자세한 배경은 [이 이슈](https://github.com/marqo-ai/marqo/issues/35)를 참고하세요.

3. 다음 명령으로 Marqo Docker 컨테이너를 실행합니다.

    ```bash
    docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:latest
    ```

4. [Streamlit 설치 문서](https://docs.streamlit.io/library/get-started/installation)를 참고해 Streamlit을 설치합니다.

5. Marqo 클라이언트를 설치합니다.

    ```bash
    pip install marqo
    ```

    Anaconda를 사용한다면 현재 Anaconda 환경 안에 Marqo를 설치했는지 확인하세요.

6. `streamlit_marqo_demo.py`가 있는 디렉터리에서 다음 명령으로 Streamlit 애플리케이션을 시작합니다.

    ```bash
    streamlit run streamlit_marqo_demo.py
    ```

자세한 문서는 다음 링크를 참고하세요.

- [Marqo 문서](https://docs.marqo.ai/)
- [Streamlit 문서](https://docs.streamlit.io/)

## 사용법

코드를 직접 살펴보면 Marqo 함수가 어떻게 사용되는지 이해하는 데 도움이 됩니다.
