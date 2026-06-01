# Simple Wikipedia 데모

## 시작하기

1. [Simple Wikipedia](https://drive.google.com/file/d/1OEqXeIdqaZb6BwzKIgw8G_sDi91fBawt/view?usp=sharing) 데이터셋을 다운로드합니다.

2. Marqo를 실행합니다.

    ```bash
    docker rm -f marqo;docker run --name marqo -it -p 8882:8882 --add-host host.docker.internal:host-gateway marqoai/marqo:latest
    ```

    자세한 내용은 [시작 가이드](index.md)를 참고하세요.

3. 다음 명령으로 `simple_wiki_demo.py` 스크립트를 실행합니다. 컴퓨터 성능에 따라 인덱싱에 시간이 걸릴 수 있습니다.

    ```bash
    python3 simple_wiki_demo.py
    ```
