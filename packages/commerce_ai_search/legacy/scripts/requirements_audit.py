from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.local_acceptance import build_source_fingerprint

DERIVED_OUTPUT_PLACEHOLDERS = {
    "<quality_report.md>": "quality-report.md",
    "<csv_index.md>": "csv-index.md",
    "<env_check.md>": "env-check.md",
    "<load_text.md>": "load-text.md",
    "<load_image.md>": "load-image.md",
    "<load_mixed.md>": "load-mixed.md",
    "<load_mixed_traffic.md>": "load-mixed-traffic.md",
    "<api_scale.md>": "api-scale.md",
    "<representative_sites.md>": "representative-sites.md",
    "<security.md>": "security.md",
}


REQUIREMENTS = [
    {
        "id": "acceptance_01_text_search",
        "group": "plan_acceptance",
        "title": "텍스트 검색이 정상 동작한다",
        "local_checks": ["acceptance_check", "quality_report"],
        "operational_checks": ["api_smoke", "quality_report", "load_text_100_concurrent"],
        "static_paths": ["app/search_service.py", "app/engine.py", "app/query_normalizer.py"],
    },
    {
        "id": "acceptance_02_image_search",
        "group": "plan_acceptance",
        "title": "이미지 업로드 검색이 정상 동작한다",
        "local_checks": ["acceptance_check", "widget_dom_check"],
        "operational_checks": ["api_smoke", "quality_report", "load_image_30_concurrent"],
        "static_paths": ["app/image_validation.py", "app/main.py"],
    },
    {
        "id": "acceptance_03_mixed_search",
        "group": "plan_acceptance",
        "title": "텍스트+이미지 혼합 검색이 정상 동작한다",
        "local_checks": ["acceptance_check", "quality_report"],
        "operational_checks": ["api_smoke", "quality_report", "load_mixed_30_concurrent"],
        "static_paths": ["app/search_service.py", "app/engine.py"],
    },
    {
        "id": "acceptance_04_top3",
        "group": "plan_acceptance",
        "title": "검색 결과 상위 3개 상품이 별도 노출된다",
        "local_checks": ["acceptance_check", "widget_dom_check"],
        "operational_checks": ["api_smoke", "quality_report"],
        "static_paths": ["app/search_service.py", "widget/widget.js"],
    },
    {
        "id": "acceptance_05_related_items",
        "group": "plan_acceptance",
        "title": "관련 상품 리스트가 출력된다",
        "local_checks": ["acceptance_check", "widget_dom_check"],
        "operational_checks": ["api_smoke", "quality_report"],
        "static_paths": ["app/search_service.py", "widget/widget.js"],
    },
    {
        "id": "acceptance_06_categories",
        "group": "plan_acceptance",
        "title": "비슷한 카테고리 추천이 출력된다",
        "local_checks": ["acceptance_check", "quality_report", "widget_dom_check"],
        "operational_checks": ["api_smoke", "quality_report"],
        "static_paths": ["app/search_service.py", "widget/widget.js"],
    },
    {
        "id": "acceptance_07_product_click",
        "group": "plan_acceptance",
        "title": "상품 클릭 시 기존 상품 상세 페이지로 이동한다",
        "local_checks": ["acceptance_check", "widget_dom_check"],
        "operational_checks": ["api_smoke", "representative_mall_sites"],
        "static_paths": ["app/search_service.py", "widget/widget.js"],
    },
    {
        "id": "acceptance_08_active_only",
        "group": "plan_acceptance",
        "title": "판매중/노출중 상품만 검색 결과에 표시된다",
        "local_checks": ["acceptance_check", "unit_tests"],
        "operational_checks": ["api_smoke", "csv_poc_index"],
        "static_paths": ["app/models.py", "app/search_service.py", "app/sync.py"],
    },
    {
        "id": "acceptance_09_regular_sync",
        "group": "plan_acceptance",
        "title": "상품 변경 사항이 정기 동기화로 반영된다",
        "local_checks": ["acceptance_check", "unit_tests"],
        "operational_checks": ["api_smoke", "mssql_export", "mssql_view", "csv_poc_index", "server_preflight", "security"],
        "static_paths": ["app/sync.py", "app/sync_worker.py", "deploy/systemd/haeorum-ai-sync.service"],
    },
    {
        "id": "acceptance_10_representative_sites",
        "group": "plan_acceptance",
        "title": "대표 가맹점 사이트에서 정상 동작한다",
        "local_checks": ["acceptance_check", "widget_dom_check"],
        "operational_checks": ["mall_config_build", "mall_config", "representative_mall_sites"],
        "static_paths": [
            "scripts/mall_config_check.py",
            "scripts/representative_site_check.py",
            "contracts/representative_sites.example.json",
        ],
    },
    {
        "id": "deliverable_01_marqo_config",
        "group": "development_deliverable",
        "title": "Marqo 기반 AI 검색엔진 설치 구성",
        "local_checks": ["contract_check"],
        "operational_checks": ["marqo_resource", "csv_poc_index"],
        "static_paths": ["compose-haeorum-marqo.yaml", "app/engine.py"],
    },
    {
        "id": "deliverable_02_fastapi_server",
        "group": "development_deliverable",
        "title": "FastAPI AI 검색 API 서버",
        "local_checks": ["contract_check", "unit_tests"],
        "operational_checks": ["server_preflight", "env_preflight", "api_smoke"],
        "static_paths": ["app/main.py", "requirements.txt", "Dockerfile"],
    },
    {
        "id": "deliverable_03_mssql_sync",
        "group": "development_deliverable",
        "title": "MSSQL 상품 데이터 동기화 프로그램",
        "local_checks": ["unit_tests"],
        "operational_checks": ["mssql_export", "mssql_view", "server_preflight", "api_smoke"],
        "static_paths": ["app/sync.py", "scripts/mssql_view_check.py", "scripts/mssql_export_csv.py"],
    },
    {
        "id": "deliverable_04_csv_poc_index",
        "group": "development_deliverable",
        "title": "CSV 기반 PoC 색인 스크립트",
        "local_checks": ["csv_index_dry_run", "quality_report"],
        "operational_checks": ["poc_dataset", "quality_report", "csv_poc_index"],
        "static_paths": ["scripts/csv_index.py", "sample_products.csv"],
    },
    {
        "id": "deliverable_05_image_server_module",
        "group": "development_deliverable",
        "title": "이미지 서버 연동 모듈",
        "local_checks": ["unit_tests"],
        "operational_checks": ["image_urls", "csv_poc_index"],
        "static_paths": ["app/image_probe.py", "scripts/image_url_check.py"],
    },
    {
        "id": "deliverable_06_text_api",
        "group": "development_deliverable",
        "title": "텍스트 검색 API",
        "local_checks": ["acceptance_check", "contract_check"],
        "operational_checks": ["api_smoke", "load_text_100_concurrent"],
        "static_paths": ["app/main.py", "app/query_normalizer.py", "contracts/text_request.json"],
    },
    {
        "id": "deliverable_07_image_api",
        "group": "development_deliverable",
        "title": "이미지 검색 API",
        "local_checks": ["acceptance_check", "contract_check"],
        "operational_checks": ["api_smoke", "load_image_30_concurrent"],
        "static_paths": ["app/main.py", "contracts/image_request.json"],
    },
    {
        "id": "deliverable_08_mixed_api",
        "group": "development_deliverable",
        "title": "텍스트+이미지 혼합 검색 API",
        "local_checks": ["acceptance_check", "contract_check"],
        "operational_checks": ["api_smoke", "load_mixed_30_concurrent"],
        "static_paths": ["app/main.py", "contracts/mixed_request.json"],
    },
    {
        "id": "deliverable_09_widget",
        "group": "development_deliverable",
        "title": "JS 검색 위젯",
        "local_checks": ["widget_dom_check", "widget_js_syntax"],
        "operational_checks": ["representative_mall_sites"],
        "static_paths": ["widget/widget.js", "contracts/widget_init.example.html"],
    },
    {
        "id": "deliverable_10_result_ui",
        "group": "development_deliverable",
        "title": "검색 결과 UI",
        "local_checks": ["widget_dom_check"],
        "operational_checks": ["representative_mall_sites"],
        "static_paths": ["widget/widget.js"],
    },
    {
        "id": "deliverable_11_admin_sync_api",
        "group": "development_deliverable",
        "title": "관리자/동기화 API",
        "local_checks": ["contract_check", "unit_tests"],
        "operational_checks": ["api_smoke"],
        "static_paths": ["app/main.py", "app/sync.py"],
    },
    {
        "id": "deliverable_12_logs",
        "group": "development_deliverable",
        "title": "검색 로그/오류 로그",
        "local_checks": ["unit_tests", "acceptance_check"],
        "operational_checks": ["api_smoke", "security"],
        "static_paths": ["app/search_service.py", "app/query_normalizer.py", "app/metrics.py", "scripts/search_insights.py"],
    },
    {
        "id": "deliverable_13_load_reports",
        "group": "development_deliverable",
        "title": "부하 테스트 결과 리포트",
        "local_checks": ["unit_tests"],
        "operational_checks": [
            "load_text_100_concurrent",
            "load_image_30_concurrent",
            "load_mixed_30_concurrent",
            "load_mixed_traffic_850_active_users",
            "api_scale_comparison",
        ],
        "static_paths": ["scripts/load_test.py", "scripts/load_compare.py"],
    },
    {
        "id": "deliverable_14_operations_docs",
        "group": "development_deliverable",
        "title": "설치 및 운영 문서",
        "local_checks": ["contract_check", "operational_bundle_check"],
        "operational_checks": ["server_preflight", "env_preflight", "security", "marqo_resource"],
        "static_paths": ["README.md", "OPERATIONS.md", "INTEGRATION.md", "deploy/nginx/haeorum-ai-search.conf"],
    },
    {
        "id": "deliverable_15_mall_config",
        "group": "development_deliverable",
        "title": "1,700개 가맹점 공통 설정",
        "local_checks": ["acceptance_check", "unit_tests"],
        "operational_checks": ["mall_config_build", "mall_config", "representative_mall_sites"],
        "static_paths": ["app/config.py", "scripts/mall_config_builder.py", "scripts/mall_config_check.py", "sample_malls.json"],
    },
    {
        "id": "operational_01_sync_failure_alerting",
        "group": "nonfunctional_operation",
        "title": "동기화 실패 알림이 운영에서 구성된다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": ["security"],
        "static_paths": [
            "app/sync.py",
            "scripts/security_check.py",
            "contracts/operational_evidence.config.example.json",
        ],
    },
    {
        "id": "operational_02_mssql_readonly_no_write",
        "group": "nonfunctional_operation",
        "title": "기존 MSSQL 원본 DB는 read-only로 접근하고 쓰기 작업을 하지 않는다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": ["mssql_view"],
        "static_paths": [
            "app/config.py",
            "app/sync.py",
            "scripts/env_check.py",
            "scripts/mssql_view_check.py",
            "scripts/mssql_export_csv.py",
            "sql/v_ai_search_products_template.sql",
        ],
    },
    {
        "id": "operational_03_sensitive_log_redaction",
        "group": "nonfunctional_operation",
        "title": "검색/클릭/오류/동기화 로그는 개인정보와 secret 원문을 저장하지 않는다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": ["api_smoke", "security"],
        "static_paths": [
            "app/search_service.py",
            "app/main.py",
            "app/sync.py",
            "scripts/security_check.py",
            "README.md",
            "OPERATIONS.md",
        ],
    },
    {
        "id": "operational_04_public_admin_access_control",
        "group": "nonfunctional_operation",
        "title": "공개 검색/클릭 API와 관리자 API는 API key, Origin, admin key 검증을 강제한다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": ["api_smoke", "security"],
        "static_paths": [
            "app/security.py",
            "app/main.py",
            "app/config.py",
            "scripts/api_smoke_test.py",
            "scripts/security_check.py",
            "contracts/openapi.json",
        ],
    },
    {
        "id": "operational_05_rate_limit_cache_scale_controls",
        "group": "nonfunctional_operation",
        "title": "이미지 검색 rate limit, 큐, 캐시, 850명 부하 확장성 증거를 확인한다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": [
            "api_smoke",
            "env_preflight",
            "load_text_100_concurrent",
            "load_image_30_concurrent",
            "load_mixed_30_concurrent",
            "load_mixed_traffic_850_active_users",
            "api_scale_comparison",
        ],
        "static_paths": [
            "app/main.py",
            "app/rate_limit.py",
            "app/cache.py",
            "app/concurrency.py",
            "scripts/load_test.py",
            "scripts/load_compare.py",
        ],
    },
    {
        "id": "operational_06_domain_filters_and_product_policy",
        "group": "nonfunctional_operation",
        "title": "카테고리, 가격, 수량, 납기, 속성 필터와 상품 노출 정책을 검증한다",
        "local_checks": ["unit_tests", "contract_check", "acceptance_check"],
        "operational_checks": ["api_smoke", "csv_poc_index", "representative_mall_sites"],
        "static_paths": [
            "app/models.py",
            "app/search_service.py",
            "app/engine.py",
            "app/sync.py",
            "scripts/api_smoke_test.py",
            "scripts/quality_report.py",
        ],
    },
    {
        "id": "operational_07_regular_sync_and_daily_reconciliation",
        "group": "nonfunctional_operation",
        "title": "1시간 변경 동기화와 매일 새벽 전체 상태 검증 배치를 운영에서 확인한다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": ["api_smoke", "csv_poc_index", "security"],
        "static_paths": [
            "app/sync.py",
            "app/sync_worker.py",
            "deploy/systemd/haeorum-ai-sync.service",
            "deploy/systemd/haeorum-ai-reindex.service",
            "deploy/systemd/haeorum-ai-reindex.timer",
            "scripts/security_check.py",
        ],
    },
    {
        "id": "architecture_01_search_engine_abstraction",
        "group": "architecture_risk",
        "title": "Marqo OSS 중단 리스크에 대비해 검색엔진 교체 가능한 구조를 둔다",
        "local_checks": ["unit_tests", "contract_check"],
        "operational_checks": ["env_preflight"],
        "static_paths": [
            "app/engine.py",
            "app/engine_factory.py",
            "scripts/env_check.py",
            "README.md",
        ],
    },
]


def load_json_report(path: str | Path) -> dict[str, Any] | None:
    target = Path(path)
    if not str(path) or not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"ok": False, "parse_error": "invalid JSON"}
    return data if isinstance(data, dict) else {"ok": False, "parse_error": "JSON root must be an object"}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    local_report = load_json_report(args.local_acceptance_report)
    operational_report = load_json_report(args.operational_readiness_report)
    evidence_collection_path = str(getattr(args, "evidence_collection_report", "") or "")
    evidence_collection_report = load_json_report(evidence_collection_path) if evidence_collection_path else None
    local_gate = local_acceptance_gate(local_report)
    trusted_local_report = local_report if local_gate["ok"] else None
    evidence_gate = evidence_collection_gate(evidence_collection_report)
    collection_steps = evidence_collection_steps(evidence_collection_report)
    items = [
        audit_requirement(requirement, trusted_local_report, operational_report, collection_steps)
        for requirement in REQUIREMENTS
    ]
    status_counts = Counter(str(item["status"]) for item in items)
    blockers = operational_blockers(items)
    requirements_ok = all(item["status"] == "passed" for item in items)
    summary = completion_summary(
        items=items,
        status_counts=status_counts,
        blockers=blockers,
        requirements_ok=requirements_ok,
        local_gate=local_gate,
        evidence_gate=evidence_gate,
        operational_report=operational_report,
        evidence_collection_report=evidence_collection_report,
    )
    report = {
        "ok": summary["completion_ready"],
        "completion_ready": summary["completion_ready"],
        "requirements_ok": requirements_ok,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "local_acceptance_report": report_path_status(args.local_acceptance_report, local_report),
        "local_acceptance_gate": local_gate,
        "operational_readiness_report": report_path_status(args.operational_readiness_report, operational_report),
        "evidence_collection_report": report_path_status(evidence_collection_path, evidence_collection_report),
        "evidence_collection_status_counts": evidence_collection_status_counts(evidence_collection_report),
        "evidence_collection_gate": evidence_gate,
        "status_counts": dict(sorted(status_counts.items())),
        "operational_blockers": blockers,
        "items": items,
    }
    return sanitize_report_for_deployment(
        report,
        deployment_project_root=getattr(args, "blocker_checklist_project_root", ""),
        deployment_evidence_dir=getattr(args, "blocker_checklist_evidence_dir", ""),
    )


def completion_summary(
    *,
    items: list[dict[str, Any]],
    status_counts: Counter[str],
    blockers: list[dict[str, Any]],
    requirements_ok: bool,
    local_gate: dict[str, Any],
    evidence_gate: dict[str, Any],
    operational_report: dict[str, Any] | None,
    evidence_collection_report: dict[str, Any] | None,
) -> dict[str, Any]:
    operational_readiness_ok = operational_report.get("ok") if isinstance(operational_report, dict) else None
    evidence_collection_complete = (
        evidence_collection_report.get("evidence_complete") if isinstance(evidence_collection_report, dict) else None
    )
    completion_ready = (
        requirements_ok
        and local_gate.get("ok") is True
        and operational_readiness_ok is True
        and evidence_gate.get("ok") is True
    )
    not_complete_reasons = []
    if local_gate.get("ok") is not True:
        not_complete_reasons.append("local_acceptance_gate")
    if requirements_ok is not True:
        not_complete_reasons.append("requirements_not_passed")
    if operational_readiness_ok is not True:
        not_complete_reasons.append("operational_readiness")
    if evidence_gate.get("ok") is not True:
        not_complete_reasons.append("evidence_collection_gate")

    return {
        "completion_ready": completion_ready,
        "requirement_count": len(items),
        "requirements_ok": requirements_ok,
        "status_counts": dict(sorted(status_counts.items())),
        "operational_blocker_count": len(blockers),
        "local_acceptance_gate_ok": local_gate.get("ok"),
        "evidence_collection_gate_ok": evidence_gate.get("ok"),
        "evidence_collection_complete": evidence_collection_complete,
        "operational_readiness_ok": operational_readiness_ok,
        "not_complete_reasons": not_complete_reasons,
    }


def report_path_status(path: str | Path, report: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "path": str(path),
        "present": report is not None,
        "ok": report.get("ok") if isinstance(report, dict) else None,
        "parse_error": report.get("parse_error") if isinstance(report, dict) else None,
    }


def local_acceptance_gate(report: dict[str, Any] | None) -> dict[str, Any]:
    expected_fingerprint = build_source_fingerprint()
    fingerprint = report.get("source_fingerprint") if isinstance(report, dict) else None
    source_fingerprint_match = fingerprint == expected_fingerprint
    problems = []

    if not isinstance(report, dict):
        problems.append("missing")
    else:
        if report.get("parse_error"):
            problems.append("parse_error")
        if report.get("ok") is not True:
            problems.append("ok")
        if report.get("local_only") is not True:
            problems.append("local_only")
        if report.get("not_operational_readiness") is not True:
            problems.append("not_operational_readiness")
        if not isinstance(fingerprint, dict):
            problems.append("source_fingerprint")
        elif not source_fingerprint_match:
            problems.append("source_fingerprint_mismatch")

    return {
        "present": isinstance(report, dict),
        "ok": not problems,
        "problems": problems,
        "source_fingerprint_match": source_fingerprint_match,
        "source_fingerprint_digest": fingerprint.get("digest") if isinstance(fingerprint, dict) else None,
        "expected_source_fingerprint_digest": expected_fingerprint.get("digest"),
        "resolution": (
            "Regenerate local acceptance from the current source tree with "
            "`python scripts/local_acceptance.py --output logs/local-acceptance.json "
            "--markdown-output logs/local-acceptance.md`."
        ),
    }


def audit_requirement(
    requirement: dict[str, Any],
    local_report: dict[str, Any] | None,
    operational_report: dict[str, Any] | None,
    collection_steps: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    local_checks = [local_check_status(local_report, name) for name in requirement.get("local_checks", [])]
    operational_checks = [
        operational_check_status(operational_report, collection_steps, name)
        for name in requirement.get("operational_checks", [])
    ]
    static_paths = [static_path_status(path) for path in requirement.get("static_paths", [])]

    local_ok = bool(local_checks) and all(check["ok"] is True for check in local_checks)
    operational_ok = bool(operational_checks) and all(check["status"] == "passed" for check in operational_checks)
    operational_failed = any(check["status"] == "failed" for check in operational_checks)
    static_ok = bool(static_paths) and all(item["exists"] for item in static_paths)

    if operational_ok:
        status = "passed"
    elif operational_failed:
        status = "failed"
    elif local_ok:
        status = "local_only"
    elif static_ok:
        status = "implemented_unverified"
    else:
        status = "missing"

    return {
        "id": requirement["id"],
        "group": requirement["group"],
        "title": requirement["title"],
        "status": status,
        "local_checks": local_checks,
        "operational_checks": operational_checks,
        "static_paths": static_paths,
    }


def local_check_status(report: dict[str, Any] | None, name: str) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"name": name, "present": False, "ok": None}
    for check in report.get("checks", []):
        if isinstance(check, dict) and check.get("name") == name:
            return {"name": name, "present": True, "ok": check.get("ok") is True}
    return {"name": name, "present": False, "ok": None}


def evidence_collection_steps(report: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(report, dict):
        return {}
    resolution_map = evidence_collection_resolution_map(report)
    steps = {}
    for step in report.get("steps", []):
        if not isinstance(step, dict) or not step.get("name"):
            continue
        enriched = dict(step)
        enriched["missing_config_resolutions"] = {
            name: resolution_map["config"].get(name)
            for name in step.get("missing_config") or []
            if resolution_map["config"].get(name)
        }
        enriched["missing_input_file_resolutions"] = {
            name: resolution_map["file"].get(name)
            for name in step.get("missing_input_files") or []
            if resolution_map["file"].get(name)
        }
        steps[str(step.get("name"))] = enriched
    return steps


def evidence_collection_resolution_map(report: dict[str, Any]) -> dict[str, dict[str, str]]:
    blocking_inputs = report.get("blocking_inputs") if isinstance(report.get("blocking_inputs"), dict) else {}
    return {
        "config": blocking_input_resolution_map(blocking_inputs.get("missing_config")),
        "file": blocking_input_resolution_map(blocking_inputs.get("missing_files")),
    }


def blocking_input_resolution_map(entries: Any) -> dict[str, str]:
    resolutions: dict[str, str] = {}
    if not isinstance(entries, list):
        return resolutions
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        resolution = str(entry.get("resolution") or "")
        if name and resolution:
            resolutions[name] = resolution
    return resolutions


def evidence_collection_status_counts(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    counts = report.get("status_counts")
    return counts if isinstance(counts, dict) else {}


def evidence_collection_gate(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {
            "present": False,
            "ok": True,
            "problems": [],
            "dry_run": None,
            "evidence_complete": None,
            "failed_steps": [],
            "skipped_steps": [],
            "incomplete_steps": [],
            "status_counts": {},
        }

    status_counts = evidence_collection_status_counts(report)
    failed_steps = string_list(report.get("failed_steps"))
    skipped_steps = string_list(report.get("skipped_steps"))
    incomplete_steps = incomplete_evidence_collection_steps(report)
    problems = []

    if report.get("parse_error"):
        problems.append("parse_error")
    if report.get("ok") is not True:
        problems.append("ok")
    if report.get("dry_run") is True:
        problems.append("dry_run")
    if report.get("evidence_complete") is not True:
        problems.append("evidence_complete")
    if failed_steps:
        problems.append("failed_steps")
    if skipped_steps:
        problems.append("skipped_steps")
    if incomplete_steps:
        problems.append("incomplete_steps")
    for name in ["failed", "skipped", "planned", "pending"]:
        if positive_count(status_counts.get(name)):
            problems.append(f"status_counts.{name}")

    return {
        "present": True,
        "ok": not problems,
        "problems": problems,
        "dry_run": report.get("dry_run"),
        "evidence_complete": report.get("evidence_complete"),
        "failed_steps": failed_steps,
        "skipped_steps": skipped_steps,
        "incomplete_steps": incomplete_steps,
        "status_counts": status_counts,
    }


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def positive_count(value: Any) -> bool:
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int | float):
        return value > 0
    if isinstance(value, str):
        try:
            return float(value) > 0
        except ValueError:
            return False
    return False


def incomplete_evidence_collection_steps(report: dict[str, Any]) -> list[dict[str, str]]:
    steps = report.get("steps")
    if not isinstance(steps, list):
        return []
    incomplete_statuses = {"failed", "skipped", "planned", "pending"}
    incomplete = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        status = str(step.get("status") or "")
        if status not in incomplete_statuses:
            continue
        incomplete.append(
            {
                "name": str(step.get("name") or ""),
                "status": status,
            }
        )
    return incomplete


def operational_check_status(
    report: dict[str, Any] | None,
    collection_steps: dict[str, dict[str, Any]],
    name: str,
) -> dict[str, Any]:
    collection = collection_step_status(collection_steps.get(name))
    if not isinstance(report, dict):
        return {"name": name, "present": False, "status": "missing", "ok": False, "collection": collection}
    for check in report.get("checks", []):
        if isinstance(check, dict) and check.get("name") == name:
            status = str(check.get("status") or ("passed" if check.get("ok") is True else "failed"))
            return {
                "name": name,
                "present": True,
                "status": status,
                "ok": check.get("ok") is True,
                "command_hint": check.get("command_hint"),
                "collection": collection,
            }
    return {"name": name, "present": False, "status": "missing", "ok": False, "collection": collection}


def collection_step_status(step: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(step, dict):
        return {"present": False}
    missing_config = step.get("missing_config") or []
    missing_input_files = step.get("missing_input_files") or []
    return {
        "present": True,
        "status": step.get("status"),
        "missing_config": missing_config,
        "missing_input_files": missing_input_files,
        "resolutions": collection_step_resolutions(
            missing_config,
            missing_input_files,
            step.get("missing_config_resolutions"),
            step.get("missing_input_file_resolutions"),
        ),
        "command": step.get("command"),
        "evidence_file": step.get("evidence_file"),
        "evidence_exists": step.get("evidence_exists"),
        "message": step.get("message"),
    }


def collection_step_resolutions(
    missing_config: list[Any],
    missing_input_files: list[Any],
    config_resolutions: Any,
    file_resolutions: Any,
) -> list[dict[str, str]]:
    resolutions: list[dict[str, str]] = []
    config_resolution_map = config_resolutions if isinstance(config_resolutions, dict) else {}
    file_resolution_map = file_resolutions if isinstance(file_resolutions, dict) else {}
    for name in missing_config:
        resolution = config_resolution_map.get(name)
        if resolution:
            resolutions.append({"type": "config", "name": str(name), "resolution": str(resolution)})
    for name in missing_input_files:
        resolution = file_resolution_map.get(name)
        if resolution:
            resolutions.append({"type": "file", "name": str(name), "resolution": str(resolution)})
    return resolutions


def static_path_status(path: str) -> dict[str, Any]:
    target = ROOT / path
    return {"path": path, "exists": target.exists()}


def operational_blockers(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: dict[str, dict[str, Any]] = {}
    for item in items:
        for check in item.get("operational_checks") or []:
            if check.get("status") == "passed":
                continue
            name = str(check.get("name") or "")
            if not name:
                continue
            collection = check.get("collection") or {}
            blocker = blockers.setdefault(
                name,
                {
                    "name": name,
                    "readiness_status": check.get("status"),
                    "collection_status": collection.get("status") if collection.get("present") else None,
                    "missing_config": [],
                    "missing_input_files": [],
                    "resolutions": [],
                    "command": collection.get("command"),
                    "command_hint": check.get("command_hint"),
                    "evidence_file": collection.get("evidence_file"),
                    "evidence_exists": collection.get("evidence_exists"),
                    "affected_requirements": [],
                    "next_action": "",
                },
            )
            blocker["readiness_status"] = worst_status(blocker.get("readiness_status"), check.get("status"))
            if collection.get("present"):
                blocker["collection_status"] = worst_status(blocker.get("collection_status"), collection.get("status"))
                blocker["evidence_file"] = blocker.get("evidence_file") or collection.get("evidence_file")
                blocker["evidence_exists"] = bool(blocker.get("evidence_exists") or collection.get("evidence_exists"))
                append_unique(blocker["missing_config"], collection.get("missing_config") or [])
                append_unique(blocker["missing_input_files"], collection.get("missing_input_files") or [])
                append_unique(blocker["resolutions"], collection.get("resolutions") or [])
                blocker["command"] = blocker.get("command") or collection.get("command")
            blocker["command_hint"] = blocker.get("command_hint") or check.get("command_hint")
            append_unique(blocker["affected_requirements"], [item.get("id")])

    for blocker in blockers.values():
        blocker["resolution_summary"] = summarize_resolutions(blocker.get("resolutions") or [])
        blocker["next_action"] = blocker_next_action(blocker)
    return sorted(blockers.values(), key=blocker_sort_key)


def append_unique(target: list[Any], values: list[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def worst_status(current: Any, candidate: Any) -> str:
    order = {
        "failed": 4,
        "missing": 3,
        "skipped": 2,
        "planned": 1,
        "existing": 0,
        "passed": 0,
        None: -1,
        "": -1,
    }
    current_text = str(current or "")
    candidate_text = str(candidate or "")
    return candidate_text if order.get(candidate_text, -1) > order.get(current_text, -1) else current_text


def blocker_sort_key(blocker: dict[str, Any]) -> tuple[int, str]:
    severity = 0
    if blocker.get("missing_config"):
        severity = max(severity, 3)
    if blocker.get("missing_input_files"):
        severity = max(severity, 2)
    if blocker.get("readiness_status") in {"missing", "failed"}:
        severity = max(severity, 1)
    return (-severity, str(blocker.get("name") or ""))


def blocker_next_action(blocker: dict[str, Any]) -> str:
    missing_config = blocker.get("missing_config") or []
    missing_input_files = blocker.get("missing_input_files") or []
    if blocker.get("evidence_exists") and blocker.get("readiness_status") == "failed":
        return "Open the existing evidence report details, fix the failed gate, then rerun operational readiness."
    if blocker.get("resolution_summary"):
        return "Apply the listed resolution, then rerun evidence collection."
    if missing_config and missing_input_files:
        return "Fill required secret/config values and create required input files, then rerun evidence collection."
    if missing_config:
        return "Fill required secret/config values, then rerun evidence collection."
    if missing_input_files:
        return "Create required input files or point the evidence config at existing files, then rerun evidence collection."
    if blocker.get("collection_status") == "existing":
        return "Review the existing evidence report and rerun operational readiness."
    if blocker.get("collection_status") == "planned":
        return "Run the planned evidence command and rerun operational readiness."
    if blocker.get("readiness_status") == "failed":
        return "Open the evidence report details, fix the failed gate, then rerun operational readiness."
    return "Generate the missing evidence report and rerun operational readiness."


def to_markdown(
    report: dict[str, Any],
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> str:
    lines = [
        "# Haeorum AI Search Requirements Audit",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Completion ready: `{(report.get('summary') or {}).get('completion_ready')}`",
        f"- Requirement count: `{(report.get('summary') or {}).get('requirement_count')}`",
        f"- Operational blocker count: `{(report.get('summary') or {}).get('operational_blocker_count')}`",
        f"- Not complete reasons: `{', '.join((report.get('summary') or {}).get('not_complete_reasons') or [])}`",
        f"- Local acceptance report present: `{report['local_acceptance_report']['present']}`",
        f"- Local acceptance gate: `{(report.get('local_acceptance_gate') or {}).get('ok')}`",
        f"- Operational readiness report present: `{report['operational_readiness_report']['present']}`",
        f"- Evidence collection report present: `{report['evidence_collection_report']['present']}`",
        f"- Evidence collection status counts: `{report.get('evidence_collection_status_counts') or {}}`",
        f"- Evidence collection gate: `{(report.get('evidence_collection_gate') or {}).get('ok')}`",
    ]
    local_gate = report.get("local_acceptance_gate") or {}
    local_gate_problems = local_gate.get("problems") or []
    if local_gate_problems:
        lines.append(f"- Local acceptance gate problems: `{', '.join(local_gate_problems)}`")
        lines.append(f"- Local acceptance gate resolution: {local_gate.get('resolution') or ''}")
        lines.append("- Local acceptance gate is blocking completion until the report matches the current source tree.")
    evidence_gate_problems = (report.get("evidence_collection_gate") or {}).get("problems") or []
    if evidence_gate_problems:
        lines.append(f"- Evidence collection gate problems: `{', '.join(evidence_gate_problems)}`")
    if str(deployment_project_root or "").strip():
        lines.append(f"- Run from: `{normalize_posix_path(deployment_project_root)}`")
    if str(deployment_evidence_dir or "").strip():
        lines.append(f"- Evidence output dir: `{normalize_posix_path(deployment_evidence_dir)}`")
    lines.extend(
        [
            "",
            "## Operational Blockers",
            "",
            "| Check | Readiness | Collection | Missing Config | Missing Files | Resolution | Command | Affected | Next Action |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for blocker in report.get("operational_blockers") or []:
        command = blocker.get("command") or blocker.get("command_hint")
        lines.append(
            "| {name} | `{readiness}` | `{collection}` | {missing_config} | {missing_files} | {resolution} | {command} | {affected} | {next_action} |".format(
                name=escape_markdown_cell(blocker.get("name")),
                readiness=escape_markdown_cell(blocker.get("readiness_status")),
                collection=escape_markdown_cell(blocker.get("collection_status")),
                missing_config=escape_markdown_cell(", ".join(blocker.get("missing_config") or [])),
                missing_files=escape_markdown_cell(", ".join(blocker.get("missing_input_files") or [])),
                resolution=escape_markdown_cell(blocker.get("resolution_summary")),
                command=escape_markdown_cell(render_blocker_text(command, deployment_evidence_dir)),
                affected=escape_markdown_cell(str(len(blocker.get("affected_requirements") or []))),
                next_action=escape_markdown_cell(blocker.get("next_action")),
            )
        )
    lines.extend(
        [
            "",
            "## Requirement Matrix",
            "",
        "| Group | Requirement | Status | Local | Operational | Collection Blockers |",
        "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report.get("items", []):
        lines.append(
            "| {group} | {title} | `{status}` | {local} | {operational} | {collection} |".format(
                group=escape_markdown_cell(item.get("group")),
                title=escape_markdown_cell(item.get("title")),
                status=item.get("status"),
                local=escape_markdown_cell(summarize_local_checks(item.get("local_checks") or [])),
                operational=escape_markdown_cell(summarize_operational_checks(item.get("operational_checks") or [])),
                collection=escape_markdown_cell(summarize_collection_blockers(item.get("operational_checks") or [])),
            )
        )
    return "\n".join(lines) + "\n"


def to_blocker_checklist(
    report: dict[str, Any],
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> str:
    blockers = report.get("operational_blockers") or []
    lines = [
        "# Haeorum AI Search Operational Blocker Checklist",
        "",
        f"- OK: `{report.get('ok')}`",
        f"- Blockers: `{len(blockers)}`",
        f"- Local acceptance gate: `{(report.get('local_acceptance_gate') or {}).get('ok')}`",
        f"- Evidence collection gate: `{(report.get('evidence_collection_gate') or {}).get('ok')}`",
        "- Commands may be redacted. Fill the listed config/env/file inputs, then rerun evidence collection.",
    ]
    local_gate = report.get("local_acceptance_gate") or {}
    local_gate_problems = local_gate.get("problems") or []
    if local_gate_problems:
        lines.append(f"- Local acceptance gate problems: `{', '.join(local_gate_problems)}`")
        lines.append(f"- Local acceptance gate resolution: {local_gate.get('resolution') or ''}")
        lines.append("- Local acceptance gate is blocking completion until the report matches the current source tree.")
    evidence_gate_problems = (report.get("evidence_collection_gate") or {}).get("problems") or []
    if evidence_gate_problems:
        lines.append(f"- Evidence collection gate problems: `{', '.join(evidence_gate_problems)}`")
    if str(deployment_project_root or "").strip():
        lines.append(f"- Run from: `{normalize_posix_path(deployment_project_root)}`")
    if str(deployment_evidence_dir or "").strip():
        lines.append(f"- Evidence output dir: `{normalize_posix_path(deployment_evidence_dir)}`")
    lines.append("")
    if not blockers:
        if local_gate_problems:
            lines.append(
                "Local acceptance gate is blocking completion. Regenerate local acceptance from the current source tree, then rerun requirements audit.\n"
            )
            if not evidence_gate_problems:
                return "\n".join(lines)
        if evidence_gate_problems:
            lines.append(
                "Evidence collection gate is blocking completion. Rerun the collector without dry-run and resolve failed, skipped, planned, or pending steps.\n"
            )
            return "\n".join(lines)
        lines.append("No operational blockers remain.\n")
        return "\n".join(lines)

    for index, blocker in enumerate(blockers, start=1):
        lines.extend(
            [
                f"## {index}. {blocker.get('name')}",
                "",
                f"- Readiness: `{blocker.get('readiness_status')}`",
                f"- Collection: `{blocker.get('collection_status')}`",
                f"- Evidence: `{render_blocker_text(blocker.get('evidence_file'), deployment_evidence_dir)}`",
                f"- Affected requirements: `{len(blocker.get('affected_requirements') or [])}`",
                f"- Next action: {blocker.get('next_action') or ''}",
                "",
            ]
        )
        missing_config = blocker.get("missing_config") or []
        missing_files = blocker.get("missing_input_files") or []
        if missing_config or missing_files:
            lines.extend(["### Inputs", ""])
            for name in missing_config:
                lines.append(f"- [ ] config `{name}`")
            for name in missing_files:
                lines.append(f"- [ ] file `{name}`")
            lines.append("")
        resolutions = blocker.get("resolutions") or []
        if resolutions:
            lines.extend(["### Resolution", ""])
            for item in resolutions:
                lines.append(
                    "- {type} `{name}`: {resolution}".format(
                        type=item.get("type", ""),
                        name=item.get("name", ""),
                        resolution=item.get("resolution", ""),
                    )
                )
            lines.append("")
        command = blocker.get("command")
        command_hint = blocker.get("command_hint")
        if command:
            lines.extend(
                [
                    "### Collector Command",
                    "",
                    "```text",
                    render_blocker_text(command, deployment_evidence_dir),
                    "```",
                    "",
                ]
            )
        if command_hint and command_hint != command:
            lines.extend(
                [
                    "### Readiness Command Template",
                    "",
                    "```text",
                    render_blocker_text(command_hint, deployment_evidence_dir),
                    "```",
                    "",
                ]
            )
    return "\n".join(lines)


def render_blocker_text(value: Any, deployment_evidence_dir: str | Path = "") -> str:
    text = normalize_python_invocation(str(value or ""))
    if str(deployment_evidence_dir or "").strip():
        text = rewrite_evidence_paths(text, deployment_evidence_dir)
        text = replace_derived_output_placeholders(text, deployment_evidence_dir)
    return text


def normalize_python_invocation(text: str) -> str:
    return re.sub(r"[A-Za-z]:\\[^\r\n\"']*?python(?:\.exe)?(?=\s+scripts[\\/])", "python", text)


def rewrite_evidence_paths(text: str, deployment_evidence_dir: str | Path) -> str:
    patterns = [
        r"[A-Za-z]:[\\/][^\r\n\"'` ]*?[\\/]HaeorumAISearch[\\/](?:logs|reports(?:[\\/]evidence)?)[\\/](?P<filename>[^\r\n\"'` ]+)",
        r"(?<![/\\])examples[\\/]HaeorumAISearch[\\/](?:logs|reports(?:[\\/]evidence)?)[\\/](?P<filename>[^\r\n\"'` ]+)",
    ]
    result = text
    for pattern in patterns:
        result = re.sub(
            pattern,
            lambda match: join_posix_path(deployment_evidence_dir, match.group("filename")),
            result,
        )
    return result


def sanitize_report_for_deployment(
    value: Any,
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> Any:
    if not str(deployment_project_root or "").strip() and not str(deployment_evidence_dir or "").strip():
        return value
    if isinstance(value, dict):
        return {
            key: sanitize_report_for_deployment(
                item,
                deployment_project_root=deployment_project_root,
                deployment_evidence_dir=deployment_evidence_dir,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            sanitize_report_for_deployment(
                item,
                deployment_project_root=deployment_project_root,
                deployment_evidence_dir=deployment_evidence_dir,
            )
            for item in value
        ]
    if isinstance(value, str):
        return sanitize_report_string(value, deployment_project_root, deployment_evidence_dir)
    return value


def sanitize_report_string(
    value: str,
    deployment_project_root: str | Path = "",
    deployment_evidence_dir: str | Path = "",
) -> str:
    text = normalize_python_invocation(value)
    if str(deployment_evidence_dir or "").strip():
        text = rewrite_evidence_paths(text, deployment_evidence_dir)
        text = replace_derived_output_placeholders(text, deployment_evidence_dir)
    if str(deployment_project_root or "").strip():
        text = rewrite_project_paths(text, deployment_project_root)
    return text


def rewrite_project_paths(text: str, deployment_project_root: str | Path) -> str:
    target_root = normalize_posix_path(deployment_project_root).rstrip("/")
    if not target_root:
        return text
    result = text
    for candidate in [str(ROOT), ROOT.as_posix()]:
        if candidate:
            result = result.replace(candidate, target_root)
    for candidate in [r"examples\HaeorumAISearch", "examples/HaeorumAISearch"]:
        result = replace_relative_project_path(result, candidate, target_root)
    result = result.replace(target_root + "\\", target_root + "/")
    return re.sub(
        re.escape(target_root) + r"(?P<tail>[^\s\"'`]*)",
        lambda match: target_root + match.group("tail").replace("\\", "/"),
        result,
    )


def replace_relative_project_path(text: str, candidate: str, target_root: str) -> str:
    pattern = r"(?<![/\\])" + re.escape(candidate)
    return re.sub(pattern, target_root, text)


def replace_derived_output_placeholders(text: str, deployment_evidence_dir: str | Path) -> str:
    result = text
    for placeholder, filename in DERIVED_OUTPUT_PLACEHOLDERS.items():
        result = result.replace(placeholder, '"' + join_posix_path(deployment_evidence_dir, filename) + '"')
    return result


def join_posix_path(base: str | Path, filename: str) -> str:
    text = normalize_posix_path(base).rstrip("/")
    clean_filename = normalize_posix_path(filename).lstrip("/")
    return f"{text}/{clean_filename}" if text else clean_filename


def normalize_posix_path(value: str | Path) -> str:
    return str(value).replace("\\", "/")


def summarize_local_checks(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return ""
    passed = [check["name"] for check in checks if check.get("ok") is True]
    missing = [check["name"] for check in checks if check.get("present") is not True]
    failed = [check["name"] for check in checks if check.get("present") is True and check.get("ok") is not True]
    return summarize_parts(passed, failed, missing)


def summarize_operational_checks(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return ""
    passed = [check["name"] for check in checks if check.get("status") == "passed"]
    missing = [check["name"] for check in checks if check.get("status") == "missing"]
    failed = [check["name"] for check in checks if check.get("status") == "failed"]
    return summarize_parts(passed, failed, missing)


def summarize_collection_blockers(checks: list[dict[str, Any]]) -> str:
    blockers = []
    for check in checks:
        if check.get("status") == "passed":
            continue
        collection = check.get("collection") or {}
        if collection.get("present") is not True:
            continue
        missing_config = collection.get("missing_config") or []
        missing_input_files = collection.get("missing_input_files") or []
        status = str(collection.get("status") or "")
        parts = []
        if missing_config:
            parts.append("config " + "/".join(str(item) for item in missing_config))
        if missing_input_files:
            parts.append("files " + "/".join(str(item) for item in missing_input_files))
        if status == "failed":
            message = str(collection.get("message") or "failed")
            parts.append(message)
        if parts:
            blockers.append(f"{check['name']}: " + ", ".join(parts))
    return "; ".join(blockers)


def summarize_resolutions(resolutions: list[dict[str, Any]], limit: int = 2) -> str:
    if not resolutions:
        return ""
    parts = []
    for item in resolutions[:limit]:
        prefix = f"{item.get('type')} {item.get('name')}".strip()
        parts.append(f"{prefix}: {item.get('resolution')}")
    remaining = len(resolutions) - limit
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return " ".join(parts)


def summarize_parts(passed: list[str], failed: list[str], missing: list[str]) -> str:
    parts = []
    if passed:
        parts.append("passed: " + ", ".join(passed))
    if failed:
        parts.append("failed: " + ", ".join(failed))
    if missing:
        parts.append("missing: " + ", ".join(missing))
    return "; ".join(parts)


def escape_markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", "<br>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Haeorum AI Search requirements against local and operational evidence.")
    parser.add_argument("--local-acceptance-report", default=str(ROOT / "logs" / "local-acceptance.json"))
    parser.add_argument("--operational-readiness-report", default=str(ROOT / "logs" / "operational-readiness.json"))
    parser.add_argument("--evidence-collection-report", default=str(ROOT / "logs" / "evidence-collection-plan.json"))
    parser.add_argument("--output", default="")
    parser.add_argument("--markdown-output", default="")
    parser.add_argument("--blocker-checklist-output", default="")
    parser.add_argument("--blocker-checklist-project-root", default="")
    parser.add_argument("--blocker-checklist-evidence-dir", default="")
    args = parser.parse_args()

    report = build_report(args)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    if args.markdown_output:
        Path(args.markdown_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown_output).write_text(
            to_markdown(
                report,
                deployment_project_root=args.blocker_checklist_project_root,
                deployment_evidence_dir=args.blocker_checklist_evidence_dir,
            ),
            encoding="utf-8",
        )
    if args.blocker_checklist_output:
        Path(args.blocker_checklist_output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.blocker_checklist_output).write_text(
            to_blocker_checklist(
                report,
                deployment_project_root=args.blocker_checklist_project_root,
                deployment_evidence_dir=args.blocker_checklist_evidence_dir,
            ),
            encoding="utf-8",
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
