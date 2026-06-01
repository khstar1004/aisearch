from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import statistics
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gemini_embeddings import embed_inputs_with_gemini, load_gemini_embedding_settings_from_env  # noqa: E402


DEFAULT_TEXTS = [
    "검은 우산",
    "스텐 텀블러",
    "고급 볼펜",
    "친환경 장바구니",
    "탁상 달력",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Gemini embedding latency for Haeorum AI search.")
    parser.add_argument("--texts", nargs="*", default=DEFAULT_TEXTS)
    parser.add_argument("--image-file")
    parser.add_argument("--image-url")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output")
    args = parser.parse_args()

    settings = load_gemini_embedding_settings_from_env()
    inputs = [{"text": text} for text in args.texts if str(text or "").strip()]
    if args.image_file:
        inputs.append({"image": image_file_to_data_url(Path(args.image_file))})
    if args.image_url:
        inputs.append({"image": str(args.image_url).strip()})
    if not inputs:
        raise SystemExit("No benchmark inputs configured")

    runs: list[dict[str, Any]] = []
    for run_index in range(max(1, int(args.repeat))):
        started = time.perf_counter()
        embeddings, stats = embed_inputs_with_gemini(inputs, settings=settings)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        runs.append(
            {
                "run": run_index + 1,
                "elapsed_ms": elapsed_ms,
                "stats": stats.__dict__,
                "embedding_count": len(embeddings),
                "dimensions": len(embeddings[0]) if embeddings else 0,
            }
        )

    latencies = [float(run["elapsed_ms"]) for run in runs]
    report = {
        "ok": True,
        "model": settings.model,
        "dimensions": settings.dimensions,
        "input_count": len(inputs),
        "repeat": len(runs),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "max": round(max(latencies), 3),
            "mean": round(statistics.fmean(latencies), 3),
        },
        "runs": runs,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def image_file_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


if __name__ == "__main__":
    raise SystemExit(main())
