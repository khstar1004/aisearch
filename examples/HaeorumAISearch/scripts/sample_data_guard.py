from __future__ import annotations

import csv
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
BUILTIN_SAMPLE_CSV = ROOT / "sample_products.csv"
SAMPLE_DERIVED_MIN_OVERLAP = 20
SAMPLE_DERIVED_RATIO = 0.9


def is_builtin_sample_csv_path(csv_path: str | Path | None) -> bool:
    if not csv_path:
        return False
    path = Path(csv_path)
    if path.name != BUILTIN_SAMPLE_CSV.name:
        return False
    try:
        return path.resolve() == BUILTIN_SAMPLE_CSV.resolve()
    except OSError:
        return not path.is_absolute()


@lru_cache(maxsize=1)
def builtin_sample_product_ids() -> frozenset[str]:
    if not BUILTIN_SAMPLE_CSV.exists():
        return frozenset()
    with BUILTIN_SAMPLE_CSV.open("r", encoding="utf-8-sig", newline="") as sample_file:
        reader = csv.DictReader(sample_file)
        return frozenset((row.get("product_id") or "").strip() for row in reader if (row.get("product_id") or "").strip())


def builtin_sample_dataset_profile(csv_path: str | Path | None, product_ids: Iterable[str]) -> dict[str, object]:
    ids = sorted({str(product_id).strip() for product_id in product_ids if str(product_id).strip()})
    sample_ids = builtin_sample_product_ids()
    overlap = sorted(set(ids) & set(sample_ids))
    ratio = round(len(overlap) / len(ids), 4) if ids else 0.0
    dataset_is_derived = bool(ids) and len(overlap) >= SAMPLE_DERIVED_MIN_OVERLAP and ratio >= SAMPLE_DERIVED_RATIO
    csv_is_builtin = is_builtin_sample_csv_path(csv_path)
    return {
        "csv_is_builtin_sample": csv_is_builtin,
        "dataset_is_builtin_sample_derived": dataset_is_derived,
        "builtin_sample_product_id_overlap": len(overlap),
        "product_id_count": len(ids),
        "builtin_sample_product_id_ratio": ratio,
    }


def is_local_sample_evidence(profile: dict[str, object]) -> bool:
    return bool(profile.get("csv_is_builtin_sample") or profile.get("dataset_is_builtin_sample_derived"))


def file_fingerprint(path: str | Path | None) -> dict[str, object]:
    text = str(path or "").strip()
    if not text:
        return {
            "algorithm": "sha256",
            "path": "",
            "exists": False,
            "size_bytes": 0,
            "digest": "",
        }
    target = Path(text)
    if not target.exists() or not target.is_file():
        return {
            "algorithm": "sha256",
            "path": str(target),
            "exists": False,
            "size_bytes": 0,
            "digest": "",
        }
    digest = hashlib.sha256()
    size_bytes = 0
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size_bytes += len(chunk)
            digest.update(chunk)
    return {
        "algorithm": "sha256",
        "path": str(target),
        "exists": True,
        "size_bytes": size_bytes,
        "digest": digest.hexdigest(),
    }
