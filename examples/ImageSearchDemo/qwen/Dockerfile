FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG TRANSFORMERS_PACKAGE=transformers

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --index-url ${TORCH_INDEX_URL} torch==2.8.0 torchvision==0.23.0 \
    && pip install --no-cache-dir "${TRANSFORMERS_PACKAGE}" sentence-transformers qwen-vl-utils pillow requests numpy einops timm

WORKDIR /app
COPY qwen_server.py /app/qwen_server.py

CMD ["python", "qwen_server.py", "--host", "0.0.0.0", "--port", "8098"]
