from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT_PATH = REPO_ROOT / "examples" / "HaeorumAISearch" / "scripts" / "build_query_embedding_cache.py"


def load_builder_module():
    spec = importlib.util.spec_from_file_location("haeorum_build_query_embedding_cache", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_explicit_query_candidates_include_cli_values_and_query_files(tmp_path) -> None:
    module = load_builder_module()
    query_file = tmp_path / "queries.txt"
    query_file.write_text(
        """
        # comments are ignored
        검정 우산

        스텐 텀블러
        """,
        encoding="utf-8",
    )

    candidates = module.explicit_query_candidates(["  송월   타올  "], [query_file])

    assert candidates == ["송월 타올", "검정 우산", "스텐 텀블러"]


def test_explicit_queries_take_priority_when_runtime_queries_are_capped() -> None:
    module = load_builder_module()

    queries = module.unique_runtime_queries(["스텐텀블러", "스텐 텀블러", "검정우산"], max_queries=2)

    assert len(queries) == 2
    assert any("스텐" in query and "텀블러" in query for query in queries)
