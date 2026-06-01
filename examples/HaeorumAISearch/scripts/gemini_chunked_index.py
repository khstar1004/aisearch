from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_settings, validate_marqo_url_value  # noqa: E402
from app.engine import BackendRequestError  # noqa: E402
from app.engine_factory import create_search_engine  # noqa: E402
from app.sync import CsvProductSource  # noqa: E402


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_checkpoint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--index-name", required=True)
    parser.add_argument("--marqo-url", default="http://127.0.0.1:8882")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--start-offset", type=int, default=-1)
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "reports" / "gemini-chunked-index-9000.checkpoint.json")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "gemini-chunked-index-9000.json")
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--retry-delay-seconds", type=float, default=20.0)
    args = parser.parse_args()

    started = time.perf_counter()
    settings = replace(
        load_settings(),
        engine_backend="marqo",
        embedding_backend="gemini",
        product_csv_path=Path(args.csv),
        index_name=args.index_name,
        marqo_url=validate_marqo_url_value(args.marqo_url),
        marqo_add_documents_batch_size=max(1, int(args.batch_size)),
    )
    products = CsvProductSource(settings.product_csv_path).fetch_all()
    checkpoint = read_checkpoint(args.checkpoint)
    offset = int(args.start_offset)
    if offset < 0:
        offset = int(checkpoint.get("next_offset", 0) or 0)
    offset = max(0, min(offset, len(products)))

    engine = create_search_engine(settings, preload_local_products=False)
    indexed = 0
    failed = 0
    failures: list[dict[str, Any]] = []
    try:
        while offset < len(products):
            chunk = products[offset : offset + max(1, int(args.batch_size))]
            attempt = 0
            while True:
                attempt += 1
                try:
                    result = engine.upsert_products(chunk)
                    chunk_failed = int(result.get("failed", 0) or 0)
                    if chunk_failed:
                        failed += chunk_failed
                        failures.append({"offset": offset, "attempt": attempt, "result": result})
                    indexed += int(result.get("indexed", len(chunk)) or len(chunk))
                    offset += len(chunk)
                    write_json(
                        args.checkpoint,
                        {
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                            "csv": str(settings.product_csv_path),
                            "index": settings.index_name,
                            "next_offset": offset,
                            "total": len(products),
                            "indexed": indexed,
                            "failed": failed,
                        },
                    )
                    print(f"offset={offset}/{len(products)} indexed={indexed} failed={failed}", flush=True)
                    break
                except BackendRequestError as exc:
                    if attempt > max(0, int(args.max_retries)):
                        failures.append({"offset": offset, "attempt": attempt, "error": str(exc)})
                        raise
                    print(f"retry offset={offset} attempt={attempt} error={exc}", flush=True)
                    time.sleep(max(0.0, float(args.retry_delay_seconds)) * attempt)
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()

    report = {
        "ok": failed == 0 and offset >= len(products),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "csv": str(settings.product_csv_path),
        "index": settings.index_name,
        "total": len(products),
        "next_offset": offset,
        "indexed": indexed,
        "failed": failed,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "failures": failures[:20],
    }
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
