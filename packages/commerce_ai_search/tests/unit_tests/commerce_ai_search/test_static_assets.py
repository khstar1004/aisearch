from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from commerce_ai_search import config


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = PACKAGE_ROOT.parents[1]
WIDGET_DIR = PACKAGE_ROOT / "resources" / "widget"


def read_asset(name: str) -> str:
    return (WIDGET_DIR / name).read_text(encoding="utf-8")


def test_widget_handles_missing_images_and_non_array_results() -> None:
    script = read_asset("widget.js")

    assert 'role="button" aria-label="상품 이미지 업로드"' in script
    assert 'aria-label="상품 이미지 파일 선택"' in script
    assert "Array.isArray(data.top) ? data.top : []" in script
    assert "Array.isArray(data.items) ? data.items : []" in script
    assert "const values = Array.isArray(categories) ? categories.filter(Boolean).slice(0, 15) : []" in script
    assert "replaceImageWithEmptyState" in script
    assert "isUnsafeLocalBrowserHost" in script


def test_result_page_matches_widget_url_and_category_guards() -> None:
    html = read_asset("ai-search.html")

    assert 'role="button" aria-label="상품 이미지 업로드"' in html
    assert 'aria-label="상품 이미지 파일 선택"' in html
    assert 'role="status" aria-live="polite"' in html
    assert 'details.push("카테고리 " + state.category)' in html
    assert 'button.setAttribute("aria-pressed", state.category === category ? "true" : "false")' in html
    assert "Array.isArray(categories) ? categories : []" in html
    assert "body.results-mode .hero" in html
    assert 'document.body.classList.add("results-mode")' in html
    assert "scrollIntoView" not in html
    assert "!/^https?:\\/\\//i.test(text)" in html
    assert 'authority.indexOf("@")' in html
    assert "isUnsafeLocalBrowserHost" in html
    assert "parseIpv4Host" in html


def test_packaging_data_files_include_runtime_resources() -> None:
    pyproject = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    packaged_files = {
        Path(path).as_posix()
        for paths in data_files.values()
        for path in paths
    }
    resource_files = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in (PACKAGE_ROOT / "resources").rglob("*")
        if path.is_file()
    }

    assert resource_files <= packaged_files


def test_default_resource_root_supports_source_and_installed_wheel_layout(monkeypatch, tmp_path) -> None:
    assert config.default_resource_root() == PACKAGE_ROOT / "resources"

    fake_package_root = tmp_path / "site-packages"
    fake_prefix = tmp_path / "venv"
    monkeypatch.setattr(config, "PACKAGE_ROOT", fake_package_root)
    monkeypatch.setattr(config.sys, "prefix", str(fake_prefix))

    assert config.default_resource_root() == fake_prefix / "share" / "commerce_ai_search" / "resources"


@pytest.mark.parametrize(
    ("package_relative", "example_relative"),
    [
        ("commerce_ai_search/engine.py", "examples/HaeorumAISearch/app/engine.py"),
        ("commerce_ai_search/main.py", "examples/HaeorumAISearch/app/main.py"),
        ("commerce_ai_search/metrics.py", "examples/HaeorumAISearch/app/metrics.py"),
        ("commerce_ai_search/models.py", "examples/HaeorumAISearch/app/models.py"),
        ("commerce_ai_search/rate_limit.py", "examples/HaeorumAISearch/app/rate_limit.py"),
        ("commerce_ai_search/search_service.py", "examples/HaeorumAISearch/app/search_service.py"),
        ("resources/widget/ai-search.html", "examples/HaeorumAISearch/widget/ai-search.html"),
        ("resources/widget/widget.js", "examples/HaeorumAISearch/widget/widget.js"),
        ("legacy/scripts/build_query_embedding_cache.py", "examples/HaeorumAISearch/scripts/build_query_embedding_cache.py"),
    ],
)
def test_example_docker_runtime_files_stay_synced_with_package_files(
    package_relative: str,
    example_relative: str,
) -> None:
    package_file = PACKAGE_ROOT / package_relative
    example_file = REPO_ROOT / example_relative

    assert example_file.read_bytes() == package_file.read_bytes()


def test_example_docker_config_keeps_local_root_but_shared_weight_guard() -> None:
    example_config = (REPO_ROOT / "examples/HaeorumAISearch/app/config.py").read_text(encoding="utf-8")

    assert "ROOT = Path(__file__).resolve().parents[1]" in example_config
    assert "sum must be finite" in example_config
