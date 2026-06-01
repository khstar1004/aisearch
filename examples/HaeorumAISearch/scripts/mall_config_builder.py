from __future__ import annotations

import argparse
import csv
import json
import math
import re
import secrets
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import normalize_origin_value, origin_uses_safe_public_url, validate_product_url_template_value
from app.identifiers import normalize_mall_id
from scripts.mall_config_check import (
    duplicate_value_map,
    parse_bool,
    parse_policy_list,
    product_url_template_prefix,
    redacted_api_key_duplicate,
    redacted_api_key_duplicates,
    validate_mall_config,
)


FIELD_ALIASES = {
    "mall_id": [
        "mall_id",
        "mallId",
        "mall",
        "mall_no",
        "mall_code",
        "site_id",
        "siteId",
        "site",
        "site_code",
        "shop_id",
        "shopId",
        "shop_no",
        "shop_code",
        "가맹점ID",
        "가맹점아이디",
        "가맹점",
        "사이트ID",
        "사이트아이디",
        "사이트",
        "몰ID",
        "몰아이디",
        "쇼핑몰ID",
        "쇼핑몰아이디",
    ],
    "api_key": [
        "api_key",
        "apiKey",
        "apikey",
        "public_api_key",
        "publicApiKey",
        "public_key",
        "publicKey",
        "x_api_key",
        "x-api-key",
        "API키",
        "공개API키",
        "퍼블릭키",
    ],
    "product_url_template": [
        "product_url_template",
        "productUrlTemplate",
        "product_template",
        "productTemplate",
        "product_url",
        "productUrl",
        "detail_url",
        "product_detail_url",
        "상품URL",
        "상품상세URL",
        "상품링크템플릿",
    ],
    "allowed_origins": [
        "allowed_origins",
        "allowedOrigins",
        "allowed_origin",
        "allowedOrigin",
        "origins",
        "cors_origins",
        "corsOrigins",
        "cors",
        "허용Origin",
        "허용도메인",
        "CORS",
    ],
    "origin": [
        "origin",
        "domain",
        "host",
        "url",
        "site_url",
        "siteUrl",
        "homepage",
        "homepage_url",
        "도메인",
        "사이트주소",
        "홈페이지",
        "몰주소",
        "쇼핑몰주소",
    ],
    "enabled": ["enabled", "use_yn", "display_yn", "active", "사용여부", "활성", "활성여부", "노출여부", "운영여부"],
    "excluded_product_ids": [
        "excluded_product_ids",
        "excludedProductIds",
        "blocked_product_ids",
        "blockedProductIds",
        "제외상품",
        "제외상품ID",
        "차단상품",
    ],
    "excluded_categories": [
        "excluded_categories",
        "excludedCategories",
        "blocked_categories",
        "blockedCategories",
        "제외카테고리",
        "차단카테고리",
    ],
    "hide_prices": ["hide_prices", "hidePrices", "가격숨김", "가격비공개"],
    "price_multiplier": ["price_multiplier", "priceMultiplier", "가격배율"],
    "price_adjustment": ["price_adjustment", "priceAdjustment", "가격조정"],
    "price_round_to": ["price_round_to", "priceRoundTo", "가격반올림", "가격단위"],
}


DEFAULT_ORIGIN_TEMPLATE = "https://{mall_id}.haeorumgift.com"
DEFAULT_PRODUCT_URL_TEMPLATE = "https://{mall_id}.haeorumgift.com/product_view.asp?p_idx={product_id}"


def build_mall_config_from_csv(
    csv_path: str | Path,
    *,
    origin_template: str = DEFAULT_ORIGIN_TEMPLATE,
    product_url_template: str = DEFAULT_PRODUCT_URL_TEMPLATE,
    generate_missing_api_keys: bool = False,
    sort_by_mall_id: bool = False,
    min_count: int = 1,
) -> dict[str, Any]:
    return build_mall_config_from_export(
        csv_path,
        origin_template=origin_template,
        product_url_template=product_url_template,
        generate_missing_api_keys=generate_missing_api_keys,
        sort_by_mall_id=sort_by_mall_id,
        min_count=min_count,
    )


def build_mall_config_from_export(
    input_path: str | Path,
    *,
    origin_template: str = DEFAULT_ORIGIN_TEMPLATE,
    product_url_template: str = DEFAULT_PRODUCT_URL_TEMPLATE,
    generate_missing_api_keys: bool = False,
    sort_by_mall_id: bool = False,
    min_count: int = 1,
) -> dict[str, Any]:
    rows = read_export_rows(Path(input_path))
    malls: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    generated_api_key_count = 0

    for row_index, row in enumerate(rows, start=2):
        mall, row_problems, generated_api_key = build_mall(row, row_index, origin_template, product_url_template, generate_missing_api_keys)
        problems.extend(row_problems)
        if mall:
            malls.append(mall)
        if generated_api_key:
            generated_api_key_count += 1

    duplicate_mall_ids = duplicates([str(mall.get("mall_id") or "") for mall in malls if mall.get("mall_id")])
    duplicate_api_key_values = duplicates([str(mall.get("api_key") or "") for mall in malls if mall.get("api_key")])
    duplicate_api_keys = redacted_api_key_duplicates(duplicate_api_key_values)
    duplicate_allowed_origins, duplicate_product_url_prefixes = duplicate_enabled_mall_endpoint_maps(malls)
    for mall_id in duplicate_mall_ids:
        problems.append({"field": "mall_id", "value": mall_id, "message": "duplicate mall_id"})
    for api_key in duplicate_api_key_values:
        problems.append({"field": "api_key", **redacted_api_key_duplicate(api_key), "message": "duplicate api_key"})
    for origin, mall_ids in duplicate_allowed_origins.items():
        problems.append(
            {
                "field": "allowed_origins",
                "value": origin,
                "mall_ids": mall_ids,
                "message": "allowed_origins value is reused by multiple enabled malls",
            }
        )
    for prefix, mall_ids in duplicate_product_url_prefixes.items():
        problems.append(
            {
                "field": "product_url_template",
                "value": prefix,
                "mall_ids": mall_ids,
                "message": "product_url_template prefix is reused by multiple enabled malls",
            }
        )

    enabled_count = sum(1 for mall in malls if mall.get("enabled") is True)
    if len(malls) < min_count:
        problems.append({"field": "malls", "message": f"mall count is below required minimum {min_count}"})
    if enabled_count < min_count:
        problems.append({"field": "enabled_count", "message": f"enabled mall count is below required minimum {min_count}"})

    if sort_by_mall_id:
        malls.sort(key=lambda item: str(item.get("mall_id") or ""))

    return {
        "ok": not problems,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "mall_count": len(malls),
        "enabled_count": enabled_count,
        "generated_api_key_count": generated_api_key_count,
        "cors_origins": sorted({origin for mall in malls for origin in mall.get("allowed_origins", [])}),
        "duplicate_mall_ids": duplicate_mall_ids,
        "duplicate_api_keys": duplicate_api_keys,
        "duplicate_allowed_origins": duplicate_allowed_origins,
        "duplicate_product_url_prefixes": duplicate_product_url_prefixes,
        "problems": problems,
        "config": {"malls": malls},
    }


def read_export_rows(path: Path) -> list[dict[str, str]]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return read_xlsx_rows(path)
    if suffix in {"", ".csv", ".txt"}:
        return read_csv_rows(path)
    raise ValueError(f"unsupported mall export file type: {path.suffix}")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        return [normalize_row(row) for row in reader]


def read_xlsx_rows(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as workbook:
        shared_strings = read_xlsx_shared_strings(workbook)
        sheet_path = first_xlsx_sheet_path(workbook)
        sheet = ElementTree.fromstring(workbook.read(sheet_path))
    rows = [[cell_value(cell, shared_strings) for cell in row_cells(row)] for row in sheet.findall(".//{*}sheetData/{*}row")]
    rows = [trim_trailing_empty(row) for row in rows]
    rows = [row for row in rows if any(value for value in row)]
    if not rows:
        return []
    headers = [normalize_field_name(value) for value in rows[0]]
    output: list[dict[str, str]] = []
    for row in rows[1:]:
        output.append({header: str(row[index] if index < len(row) else "").strip() for index, header in enumerate(headers) if header})
    return output


def read_xlsx_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in workbook.namelist():
        return []
    root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
    values = []
    for item in root.findall(".//{*}si"):
        text_parts = [node.text or "" for node in item.findall(".//{*}t")]
        values.append("".join(text_parts))
    return values


def first_xlsx_sheet_path(workbook: zipfile.ZipFile) -> str:
    names = set(workbook.namelist())
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        first_sheet = workbook_root.find(".//{*}sheets/{*}sheet")
        rel_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id") if first_sheet is not None else ""
        rels_root = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        for relationship in rels_root.findall(".//{*}Relationship"):
            if relationship.attrib.get("Id") == rel_id:
                target = relationship.attrib.get("Target", "")
                if target:
                    normalized = target.lstrip("/")
                    return normalized if normalized.startswith("xl/") else "xl/" + normalized
    if "xl/worksheets/sheet1.xml" in names:
        return "xl/worksheets/sheet1.xml"
    raise ValueError("xlsx workbook does not contain a worksheet")


def row_cells(row: ElementTree.Element) -> list[ElementTree.Element]:
    cells = list(row.findall("{*}c"))
    if not cells:
        return []
    width = max(cell_column_index(cell.attrib.get("r", "")) for cell in cells)
    output: list[ElementTree.Element | None] = [None] * width
    for fallback_index, cell in enumerate(cells, start=1):
        column_index = cell_column_index(cell.attrib.get("r", "")) or fallback_index
        if column_index > len(output):
            output.extend([None] * (column_index - len(output)))
        output[column_index - 1] = cell
    return [cell if cell is not None else ElementTree.Element("c") for cell in output]


def cell_column_index(reference: str) -> int:
    letters = re.match(r"([A-Za-z]+)", str(reference or ""))
    if not letters:
        return 0
    index = 0
    for char in letters.group(1).upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def cell_value(cell: ElementTree.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//{*}t")).strip()
    value_node = cell.find("{*}v")
    value = value_node.text if value_node is not None else ""
    if cell_type == "s":
        try:
            return shared_strings[int(value)].strip()
        except (ValueError, IndexError):
            return ""
    return str(value or "").strip()


def trim_trailing_empty(values: list[str]) -> list[str]:
    trimmed = list(values)
    while trimmed and not trimmed[-1]:
        trimmed.pop()
    return trimmed


def normalize_row(row: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        normalized[normalize_field_name(key)] = str(value or "").strip()
    return normalized


def normalize_field_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "_", str(value or "").strip().lower()).strip("_")


def value_for(row: dict[str, str], field: str) -> str:
    for alias in FIELD_ALIASES[field]:
        value = row.get(normalize_field_name(alias), "")
        if value:
            return value
    return ""


def build_mall(
    row: dict[str, str],
    row_index: int,
    origin_template: str,
    product_url_template: str,
    generate_missing_api_keys: bool,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], bool]:
    problems: list[dict[str, Any]] = []
    mall_id = value_for(row, "mall_id")
    if not mall_id:
        return None, [{"row": row_index, "field": "mall_id", "message": "mall_id is required"}], False
    try:
        mall_id = normalize_mall_id(mall_id, required=True)
    except ValueError as exc:
        return None, [{"row": row_index, "field": "mall_id", "value": mall_id, "message": str(exc)}], False

    enabled = parse_enabled_value(value_for(row, "enabled"))
    api_key = value_for(row, "api_key")
    generated_api_key = False
    if enabled and not api_key and generate_missing_api_keys:
        api_key = secrets.token_urlsafe(24)
        generated_api_key = True
    if enabled and not api_key:
        problems.append({"row": row_index, "mall_id": mall_id, "field": "api_key", "message": "enabled mall requires api_key"})

    origins = resolve_allowed_origins(row, mall_id, origin_template, row_index, problems)
    if enabled and not origins:
        problems.append(
            {
                "row": row_index,
                "mall_id": mall_id,
                "field": "allowed_origins",
                "message": "enabled mall requires allowed_origins",
            }
        )

    template = value_for(row, "product_url_template") or render_template(product_url_template, mall_id)
    try:
        template = validate_product_url_template_value(template, mall_id=mall_id)
    except ValueError as exc:
        problems.append({"row": row_index, "mall_id": mall_id, "field": "product_url_template", "message": str(exc)})

    mall: dict[str, Any] = {
        "mall_id": mall_id,
        "enabled": enabled,
        "api_key": api_key,
        "product_url_template": template,
        "allowed_origins": origins,
    }
    add_policy_list(mall, row, "excluded_product_ids")
    add_policy_list(mall, row, "excluded_categories")
    add_optional_bool(mall, row, "hide_prices", row_index, problems)
    add_optional_float(mall, row, "price_multiplier", row_index, problems)
    add_optional_float(mall, row, "price_adjustment", row_index, problems)
    add_optional_int(mall, row, "price_round_to", row_index, problems)
    return mall, problems, generated_api_key


def parse_enabled_value(value: str) -> bool:
    if value.strip().upper() == "N":
        return False
    return parse_bool(value, default=True)


def resolve_allowed_origins(
    row: dict[str, str],
    mall_id: str,
    origin_template: str,
    row_index: int,
    problems: list[dict[str, Any]],
) -> list[str]:
    raw_allowed = value_for(row, "allowed_origins")
    if raw_allowed:
        return normalize_origins(parse_policy_list(raw_allowed), mall_id, row_index, problems)
    raw_origin = value_for(row, "origin")
    if raw_origin:
        return normalize_origins([origin_from_site_value(raw_origin)], mall_id, row_index, problems)
    if origin_template:
        return normalize_origins([render_template(origin_template, mall_id)], mall_id, row_index, problems)
    return []


def origin_from_site_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return text


def normalize_origins(values: list[str], mall_id: str, row_index: int, problems: list[dict[str, Any]]) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    for value in values:
        try:
            origin = normalize_origin_value(value, allow_wildcard=False)
        except ValueError as exc:
            problems.append({"row": row_index, "mall_id": mall_id, "field": "allowed_origins", "value": value, "message": str(exc)})
            continue
        if not origin.startswith("https://"):
            problems.append(
                {
                    "row": row_index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "value": value,
                    "message": "allowed_origins must use https",
                }
            )
            continue
        if not origin_uses_safe_public_url(origin):
            problems.append(
                {
                    "row": row_index,
                    "mall_id": mall_id,
                    "field": "allowed_origins",
                    "value": value,
                    "message": "allowed_origins must use safe public origins",
                }
            )
            continue
        if origin not in seen:
            seen.add(origin)
            origins.append(origin)
    return origins


def render_template(template: str, mall_id: str) -> str:
    return str(template or "").replace("{mall_id}", mall_id)


def add_policy_list(mall: dict[str, Any], row: dict[str, str], field: str) -> None:
    value = value_for(row, field)
    if value:
        mall[field] = parse_policy_list(value)


def add_optional_bool(mall: dict[str, Any], row: dict[str, str], field: str, row_index: int, problems: list[dict[str, Any]]) -> None:
    value = value_for(row, field)
    if not value:
        return
    normalized = value.strip().lower()
    if normalized not in {
        "1",
        "0",
        "true",
        "false",
        "yes",
        "no",
        "y",
        "n",
        "on",
        "off",
        "사용",
        "미사용",
        "활성",
        "비활성",
        "예",
        "아니오",
        "네",
        "아니요",
    }:
        problems.append({"row": row_index, "mall_id": mall.get("mall_id"), "field": field, "message": f"{field} must be a boolean"})
        return
    mall[field] = parse_bool(value, default=False)


def add_optional_float(mall: dict[str, Any], row: dict[str, str], field: str, row_index: int, problems: list[dict[str, Any]]) -> None:
    value = value_for(row, field)
    if not value:
        return
    try:
        parsed = float(value)
    except ValueError:
        problems.append({"row": row_index, "mall_id": mall.get("mall_id"), "field": field, "message": f"{field} must be a number"})
        return
    if not math.isfinite(parsed):
        problems.append({"row": row_index, "mall_id": mall.get("mall_id"), "field": field, "message": f"{field} must be finite"})
        return
    mall[field] = parsed


def add_optional_int(mall: dict[str, Any], row: dict[str, str], field: str, row_index: int, problems: list[dict[str, Any]]) -> None:
    value = value_for(row, field)
    if not value:
        return
    try:
        mall[field] = int(value)
    except ValueError:
        problems.append({"row": row_index, "mall_id": mall.get("mall_id"), "field": field, "message": f"{field} must be an integer"})


def duplicates(values: list[str]) -> list[str]:
    seen = set()
    duplicate_values = set()
    for value in values:
        if value in seen:
            duplicate_values.add(value)
        seen.add(value)
    return sorted(duplicate_values)


def duplicate_enabled_mall_endpoint_maps(
    malls: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    origins: dict[str, set[str]] = {}
    product_url_prefixes: dict[str, set[str]] = {}
    for mall in malls:
        if mall.get("enabled") is not True:
            continue
        mall_id = str(mall.get("mall_id") or "").strip()
        if not mall_id:
            continue
        for origin in mall.get("allowed_origins") or []:
            origin_text = str(origin or "").strip().rstrip("/")
            if origin_text:
                origins.setdefault(origin_text, set()).add(mall_id)
        prefix = product_url_template_prefix(str(mall.get("product_url_template") or ""), mall_id)
        if prefix:
            product_url_prefixes.setdefault(prefix, set()).add(mall_id)
    return duplicate_value_map(origins), duplicate_value_map(product_url_prefixes)


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Haeorum malls.json from a mall/site CSV or XLSX export.")
    parser.add_argument(
        "--csv",
        "--input",
        dest="csv",
        required=True,
        help="CSV or XLSX containing mall_id/site_id, origin/domain, api_key, and optional policy columns.",
    )
    parser.add_argument("--output", required=True, help="malls.json output path.")
    parser.add_argument("--report-output", default="", help="Optional JSON report path.")
    parser.add_argument("--origin-template", default=DEFAULT_ORIGIN_TEMPLATE)
    parser.add_argument("--product-url-template", default=DEFAULT_PRODUCT_URL_TEMPLATE)
    parser.add_argument("--generate-missing-api-keys", action="store_true")
    parser.add_argument("--sort-by-mall-id", action="store_true")
    parser.add_argument("--min-count", type=int, default=1)
    args = parser.parse_args()

    report = build_mall_config_from_csv(
        args.csv,
        origin_template=args.origin_template,
        product_url_template=args.product_url_template,
        generate_missing_api_keys=args.generate_missing_api_keys,
        sort_by_mall_id=args.sort_by_mall_id,
        min_count=args.min_count,
    )
    write_json(args.output, report["config"])
    validation = validate_mall_config(Path(args.output), min_count=args.min_count)
    report = {**{key: value for key, value in report.items() if key != "config"}, "output": str(args.output), "validation": validation}
    if not validation.get("ok"):
        report["ok"] = False
        report["problems"] = [*report.get("problems", []), *validation.get("problems", [])]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report_output:
        write_json(args.report_output, report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
