from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TENANTS_ROOT = REPO_ROOT / "deployments" / "tenants"
REQUIRED_TENANT_FILES = [
    "tenant.yaml",
    "sites.json",
    "query-synonyms.json",
    "ranking.yaml",
    "theme.css",
    "sql/product-mapping.sql",
    "env.example",
]


def init_tenant(tenant_id: str, *, display_name: str | None = None, force: bool = False) -> int:
    tenant_id = normalize_tenant_id(tenant_id)
    target = TENANTS_ROOT / tenant_id
    if target.exists() and not force:
        raise SystemExit(f"tenant already exists: {target}")
    target.mkdir(parents=True, exist_ok=True)
    (target / "sql").mkdir(exist_ok=True)
    (target / "docs").mkdir(exist_ok=True)
    (target / "branding").mkdir(exist_ok=True)

    name = display_name or tenant_id
    write_if_missing(
        target / "tenant.yaml",
        f"""tenant_id: {tenant_id}
display_name: "{name}"
deployment_mode: single_tenant

runtime:
  environment: production
  public_api_base_url: "https://ai-search.example.com"
  legacy_env_prefix: ""
  future_env_prefix: "AISEARCH"

search:
  backend: marqo
  index_name: "{tenant_id}-products-v1"

catalog:
  source: mssql
  query_file: "./sql/product-mapping.sql"
  product_id_column: product_id
  updated_at_column: updated_at
  filter_by_site_id: false

sites:
  file: "./sites.json"

widget:
  script_path: "/ai-search/widget.js"
  result_page_path: "/ai-search/ai-search.html"
  future_global_name: "CommerceAISearch"
  default_target: "#aisearch-widget"
""",
        force=force,
    )
    write_json_if_missing(
        target / "sites.json",
        {
            "sites": [
                {
                    "site_id": "main",
                    "enabled": True,
                    "api_key": "replace-with-public-api-key",
                    "allowed_origins": ["https://shop.example.com"],
                    "product_url_template": "https://shop.example.com/product/{product_id}",
                }
            ]
        },
        force=force,
    )
    write_json_if_missing(target / "query-synonyms.json", {"synonyms": {}}, force=force)
    write_if_missing(
        target / "ranking.yaml",
        """profile_id: default
query_normalization:
  enable_spacing_normalization: true
  enable_common_typo_correction: true
scoring:
  low_score_threshold: 0.4
  mixed_text_weight: 0.4
  mixed_image_weight: 0.6
result_policy:
  top_count: 3
  category_suggestion_limit: 15
  collapse_by_product_group_id: true
""",
        force=force,
    )
    write_if_missing(
        target / "theme.css",
        """:root {
  --aisearch-accent: #0f766e;
  --aisearch-accent-text: #ffffff;
  --aisearch-accent-soft: #ecfdf5;
}
""",
        force=force,
    )
    write_if_missing(
        target / "sql" / "product-mapping.sql",
        """-- Replace this query with the customer's read-only product projection.
SELECT
    CAST(product_id AS varchar(100)) AS product_id,
    product_name AS product_name,
    category_name AS category_name,
    TRY_CONVERT(float, price) AS price,
    main_image_url AS main_image_url,
    product_url AS product_url,
    status AS status,
    updated_at AS updated_at,
    display_yn AS display_yn,
    CAST(site_id AS varchar(64)) AS mall_id
FROM dbo.v_ai_search_products;
""",
        force=force,
    )
    write_if_missing(
        target / "env.example",
        f"""AISEARCH_ENV=production
AISEARCH_SEARCH_ENGINE=marqo
AISEARCH_INDEX_NAME={tenant_id}-products-v1
AISEARCH_ADMIN_API_KEY=replace-with-strong-admin-key
AISEARCH_MALL_CONFIG_PATH=/etc/aisearch/tenant/sites.json
AISEARCH_QUERY_SYNONYM_PATH=/etc/aisearch/tenant/query-synonyms.json
AISEARCH_CORS_ORIGINS_FILE=/etc/aisearch/tenant/cors-origins.txt
""",
        force=force,
    )
    print(f"created tenant bundle: {target}")
    return 0


def validate_tenant(tenant_id_or_path: str) -> int:
    path = Path(tenant_id_or_path)
    if not path.exists():
        path = TENANTS_ROOT / normalize_tenant_id(tenant_id_or_path)
    problems: list[str] = []
    if not path.exists():
        problems.append(f"tenant path does not exist: {path}")
    else:
        for relative in REQUIRED_TENANT_FILES:
            if not (path / relative).exists():
                problems.append(f"missing required file: {relative}")
        for relative in ["sites.json", "query-synonyms.json"]:
            candidate = path / relative
            if candidate.exists():
                try:
                    json.loads(candidate.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    problems.append(f"invalid JSON {relative}: {exc}")
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    print(f"tenant bundle ok: {path}")
    return 0


def normalize_tenant_id(value: str) -> str:
    tenant_id = str(value or "").strip().lower().replace("_", "-")
    if not tenant_id or any(char for char in tenant_id if not (char.isalnum() or char == "-")):
        raise SystemExit("tenant_id must contain only letters, numbers, and hyphens")
    if tenant_id.startswith("-") or tenant_id.endswith("-"):
        raise SystemExit("tenant_id must not start or end with hyphen")
    return tenant_id


def write_if_missing(path: Path, text: str, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.write_text(text, encoding="utf-8")


def write_json_if_missing(path: Path, data: object, *, force: bool) -> None:
    if path.exists() and not force:
        return
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage AI Search tenant bundles.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a new tenant bundle.")
    init_parser.add_argument("tenant_id")
    init_parser.add_argument("--display-name", default=None)
    init_parser.add_argument("--force", action="store_true")

    validate_parser = subparsers.add_parser("validate", help="Validate a tenant bundle.")
    validate_parser.add_argument("tenant")

    args = parser.parse_args()
    if args.command == "init":
        return init_tenant(args.tenant_id, display_name=args.display_name, force=args.force)
    if args.command == "validate":
        return validate_tenant(args.tenant)
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

