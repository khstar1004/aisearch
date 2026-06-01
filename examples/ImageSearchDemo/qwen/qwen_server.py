#!/usr/bin/env python3
import argparse
import base64
import http.server
import io
import json
import os
import threading
import time
import traceback


MODEL_NAME = os.environ.get("QWEN_MODEL_NAME", "Qwen/Qwen3-VL-Embedding-2B")
DEVICE = os.environ.get("QWEN_DEVICE", "cpu")
EMBEDDING_DIMENSIONS = int(os.environ.get("QWEN_EMBEDDING_DIMENSIONS", "2048"))
IMAGE_USER_AGENT = "MarqoQwenImageSearchDemo/0.1"
DIRECT_SINGLE_MODAL_MODELS = ("jinaai/jina-clip",)

_model = None
_model_lock = threading.Lock()
_load_error = None


def json_dumps(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def load_model():
    global _model, _load_error
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        started = time.perf_counter()
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(
                MODEL_NAME,
                device=DEVICE,
                trust_remote_code=True,
            )
            _load_error = None
            print(
                f"Loaded {MODEL_NAME} on {DEVICE} in {(time.perf_counter() - started):.1f}s",
                flush=True,
            )
            return _model
        except Exception as exc:
            _load_error = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            raise


def prepare_inputs(inputs):
    return [prepare_input(input_item) for input_item in inputs]


def prepare_input(input_item):
    if not isinstance(input_item, dict):
        return input_item

    if MODEL_NAME.startswith(DIRECT_SINGLE_MODAL_MODELS):
        has_text = "text" in input_item and input_item.get("text") is not None
        has_image = "image" in input_item and input_item.get("image") is not None
        if has_text and not has_image:
            return input_item["text"]
        if has_image and not has_text:
            return input_item["image"]
        raise ValueError(f"{MODEL_NAME} only supports separate text or image inputs")

    prepared = dict(input_item)
    image = prepared.get("image")
    if isinstance(image, str) and image.startswith(("http://", "https://")):
        prepared["image"] = fetch_image(image)
    elif isinstance(image, str) and image.startswith("data:image/"):
        prepared["image"] = decode_data_url_image(image)
    return prepared


def fetch_image(url):
    import requests
    from PIL import Image

    response = requests.get(
        url,
        headers={"User-Agent": IMAGE_USER_AGENT},
        timeout=45,
    )
    response.raise_for_status()
    return Image.open(io.BytesIO(response.content)).convert("RGB")


def decode_data_url_image(data_url):
    from PIL import Image

    _, _, encoded = data_url.partition(",")
    if not encoded:
        raise ValueError("image data URL is missing base64 payload")
    return Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")


class QwenHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "ready": _model is not None,
                    "model": MODEL_NAME,
                    "device": DEVICE,
                    "dimensions": EMBEDDING_DIMENSIONS,
                    "loadError": _load_error,
                },
            )
            return
        self.send_json(404, {"message": "unknown endpoint"})

    def do_POST(self):
        if self.path != "/embed":
            self.send_json(404, {"message": "unknown endpoint"})
            return
        try:
            body = self.read_json()
            inputs = body.get("inputs") or []
            if not isinstance(inputs, list) or not inputs:
                raise ValueError("inputs must be a non-empty list")
            prompt = body.get("prompt")
            prompt_name = body.get("promptName") or body.get("prompt_name")
            started = time.perf_counter()
            model = load_model()
            prepared_inputs = prepare_inputs(inputs)
            encode_kwargs = {
                "normalize_embeddings": True,
                "convert_to_numpy": True,
                "show_progress_bar": False,
            }
            if prompt is not None:
                encode_kwargs["prompt"] = prompt
            if prompt_name is not None:
                encode_kwargs["prompt_name"] = prompt_name
            embeddings = model.encode(prepared_inputs, **encode_kwargs)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            vectors = embeddings.tolist()
            if vectors and len(vectors[0]) != EMBEDDING_DIMENSIONS:
                raise RuntimeError(
                    f"Expected {EMBEDDING_DIMENSIONS} dimensions, got {len(vectors[0])}"
                )
            self.send_json(
                200,
                {
                    "model": MODEL_NAME,
                    "device": DEVICE,
                    "dimensions": EMBEDDING_DIMENSIONS,
                    "count": len(vectors),
                    "elapsedMs": elapsed_ms,
                    "embeddings": vectors,
                },
            )
        except Exception as exc:
            self.send_json(
                500,
                {
                    "message": str(exc),
                    "type": exc.__class__.__name__,
                    "traceback": traceback.format_exc(limit=8),
                },
            )

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, status, data):
        payload = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8098)
    args = parser.parse_args()

    server = http.server.ThreadingHTTPServer((args.host, args.port), QwenHandler)
    print(f"Qwen embedding server: http://{args.host}:{args.port}", flush=True)
    print(f"Model: {MODEL_NAME}; device: {DEVICE}; dimensions: {EMBEDDING_DIMENSIONS}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
