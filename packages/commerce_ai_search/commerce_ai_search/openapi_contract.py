from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import ROOT
from .identifiers import MALL_ID_PATTERN_TEXT


CONTRACT_OPENAPI_PATH = ROOT / "contracts" / "openapi.json"
UNSAFE_PUBLIC_CREDENTIAL_FIELDS = {
    "api_key",
    "apiKey",
    "apikey",
    "api-key",
    "x-api-key",
    "admin_key",
    "admin-key",
    "x-admin-key",
}


def load_public_openapi_schema(path: Path | None = None) -> dict[str, Any]:
    schema_path = path or CONTRACT_OPENAPI_PATH
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validate_public_openapi_schema(schema)
    return schema


def validate_public_openapi_schema(schema: dict[str, Any]) -> None:
    search_post = schema["paths"]["/api/ai-search"]["post"]
    validate_public_credential_description("/api/ai-search", search_post)
    request_content = search_post["requestBody"]["content"]
    if "application/json" not in request_content:
        raise ValueError("/api/ai-search OpenAPI contract must include application/json")
    if "multipart/form-data" not in request_content:
        raise ValueError("/api/ai-search OpenAPI contract must include multipart/form-data")

    multipart_schema = request_content["multipart/form-data"]["schema"]
    if multipart_schema.get("additionalProperties") is not False:
        raise ValueError("/api/ai-search multipart schema must disallow additional properties")
    multipart_properties = multipart_schema.get("properties", {})
    if "site_id" not in multipart_properties:
        raise ValueError("/api/ai-search multipart schema must include site_id alias")
    image = multipart_schema.get("properties", {}).get("image", {})
    if image.get("format") != "binary":
        raise ValueError("/api/ai-search multipart image field must be binary")

    response_schema = search_post["responses"]["200"]["content"]["application/json"]["schema"]
    if response_schema.get("$ref") != "#/components/schemas/SearchResponse":
        raise ValueError("/api/ai-search 200 response must reference SearchResponse")
    if search_post["responses"].get("403", {}).get("$ref") != "#/components/responses/Forbidden":
        raise ValueError("/api/ai-search must document 403 Forbidden for origin/mall access denial")
    if search_post["responses"].get("502", {}).get("$ref") != "#/components/responses/BadGateway":
        raise ValueError("/api/ai-search must document 502 BadGateway for invalid backend responses")
    if search_post["responses"].get("503", {}).get("$ref") != "#/components/responses/ServiceUnavailable":
        raise ValueError("/api/ai-search must document 503 ServiceUnavailable for backend outages")

    click_post = schema["paths"]["/api/click-log"]["post"]
    validate_public_credential_description("/api/click-log", click_post)
    if click_post["responses"].get("400", {}).get("$ref") != "#/components/responses/BadRequest":
        raise ValueError("/api/click-log must document 400 BadRequest")
    if click_post["responses"].get("403", {}).get("$ref") != "#/components/responses/Forbidden":
        raise ValueError("/api/click-log must document 403 Forbidden for origin/mall access denial")
    if click_post["responses"].get("429", {}).get("$ref") != "#/components/responses/RateLimited":
        raise ValueError("/api/click-log must document 429 RateLimited")
    insights_get = schema["paths"].get("/admin/search-insights", {}).get("get")
    if not insights_get:
        raise ValueError("/admin/search-insights OpenAPI contract is required")
    insights_schema = insights_get["responses"]["200"]["content"]["application/json"]["schema"]
    if insights_schema.get("$ref") != "#/components/schemas/SearchInsightsReport":
        raise ValueError("/admin/search-insights 200 response must reference SearchInsightsReport")

    schemas = schema["components"]["schemas"]
    if schemas["SearchRequest"].get("additionalProperties") is not False:
        raise ValueError("SearchRequest must disallow additional properties")
    if schemas["ClickLogRequest"].get("additionalProperties") is not False:
        raise ValueError("ClickLogRequest must disallow additional properties")
    search_response = schemas["SearchResponse"]["properties"]
    if search_response["top"].get("maxItems") != 3:
        raise ValueError("SearchResponse top must declare maxItems 3")
    if search_response["suggested_categories"].get("maxItems") != 15:
        raise ValueError("SearchResponse suggested_categories must declare maxItems 15")
    if search_response["suggested_categories"].get("uniqueItems") is not True:
        raise ValueError("SearchResponse suggested_categories must declare uniqueItems true")
    search_meta = schemas["SearchMeta"]["properties"]
    if search_meta["elapsed_ms"].get("minimum") != 0:
        raise ValueError("SearchMeta elapsed_ms must declare minimum 0")
    if "embedding_backend" not in search_meta:
        raise ValueError("SearchMeta must include embedding_backend")
    if search_meta["limit"].get("minimum") != 1:
        raise ValueError("SearchMeta limit must declare minimum 1")
    if search_meta["offset"].get("minimum") != 0:
        raise ValueError("SearchMeta offset must declare minimum 0")
    if search_meta["next_offset"].get("minimum") != 0:
        raise ValueError("SearchMeta next_offset must declare minimum 0")
    for field_name in ["text_weight", "image_weight"]:
        if search_meta[field_name].get("minimum") != 0:
            raise ValueError(f"SearchMeta {field_name} must declare minimum 0")
    search_request = schemas["SearchRequest"]["properties"]
    click_request = schemas["ClickLogRequest"]["properties"]
    search_result = schemas["SearchResultItem"]["properties"]
    validate_no_unsafe_credential_fields("SearchRequest", search_request)
    validate_no_unsafe_credential_fields("ClickLogRequest", click_request)
    validate_no_unsafe_credential_fields("/api/ai-search multipart", multipart_properties)
    if search_result["product_id"].get("minLength") != 1 or search_result["product_id"].get("maxLength") != 100:
        raise ValueError("SearchResultItem product_id must declare minLength 1 and maxLength 100")
    if search_result["name"].get("minLength") != 1:
        raise ValueError("SearchResultItem name must declare minLength 1")
    if search_result["category"].get("maxLength") != 100:
        raise ValueError("SearchResultItem category must declare maxLength 100")
    if search_result["price"].get("minimum") != 0:
        raise ValueError("SearchResultItem price must declare minimum 0")
    for field_name in ["image_url", "product_url"]:
        if search_result[field_name].get("format") != "uri" or search_result[field_name].get("maxLength") != 1000:
            raise ValueError(f"SearchResultItem {field_name} must declare uri format and maxLength 1000")
    if search_result["image_url"].get("nullable") is not True:
        raise ValueError("SearchResultItem image_url must remain nullable")
    if search_result["product_url"].get("nullable") is True:
        raise ValueError("SearchResultItem product_url must not be nullable")
    if search_result["mall_id"].get("maxLength") != 64:
        raise ValueError("SearchResultItem mall_id must declare maxLength 64")
    for schema_name, properties in [
        ("SearchRequest", search_request),
        ("ClickLogRequest", click_request),
        ("SearchResultItem", search_result),
        ("SearchMeta", search_meta),
        ("/api/ai-search multipart", multipart_properties),
    ]:
        for field_name in ["mall_id", "site_id"]:
            if field_name in properties and properties[field_name].get("pattern") != MALL_ID_PATTERN_TEXT:
                raise ValueError(f"{schema_name} {field_name} must declare URL-safe mall_id pattern")
    if search_result["score"].get("minimum") != 0 or search_result["score"].get("maximum") != 1:
        raise ValueError("SearchResultItem score must declare range 0..1")
    if search_result["score_percent"].get("minimum") != 0 or search_result["score_percent"].get("maximum") != 100:
        raise ValueError("SearchResultItem score_percent must declare range 0..100")
    click_required = set(schemas["ClickLogRequest"].get("required", []))
    click_identity_any_of = [
        set(option.get("required", [])) for option in schemas["ClickLogRequest"].get("anyOf", [])
    ]
    if "product_id" not in click_required:
        raise ValueError("ClickLogRequest must require product_id")
    if {"mall_id"} not in click_identity_any_of or {"site_id"} not in click_identity_any_of:
        raise ValueError("ClickLogRequest must allow either mall_id or site_id")
    if search_request["q"].get("maxLength") != 200:
        raise ValueError("SearchRequest q must declare maxLength 200")
    if search_request["category"].get("maxLength") != 100:
        raise ValueError("SearchRequest category must declare maxLength 100")
    for field_name in ["print_method", "material", "color"]:
        if search_request[field_name].get("maxLength") != 100:
            raise ValueError(f"SearchRequest {field_name} must declare maxLength 100")
        if multipart_properties[field_name].get("maxLength") != 100:
            raise ValueError(f"/api/ai-search multipart {field_name} must declare maxLength 100")
    for field_name in ["min_price", "max_price", "quantity", "order_qty", "max_delivery_days"]:
        if field_name not in search_request or field_name not in multipart_properties:
            raise ValueError(f"SearchRequest and multipart schema must include {field_name}")
    if click_request["product_id"].get("maxLength") != 100:
        raise ValueError("ClickLogRequest product_id must declare maxLength 100")
    for field in [
        "latency_ms",
        "query_type_latency_ms",
        "cache_latency_ms",
        "slow_queries",
        "slow_search_samples",
        "mixed_weight_performance",
        "synonym_seed_candidates",
        "quality_case_candidates",
        "mixed_weight_recommendation",
    ]:
        if field not in schemas["SearchInsightsReport"]["properties"]:
            raise ValueError(f"SearchInsightsReport must include {field}")


def validate_public_credential_description(path: str, operation: dict[str, Any]) -> None:
    description = str(operation.get("description") or "")
    for expected in ["X-API-Key", "api-key", "x-api-key", "admin_key", "x-admin-key"]:
        if expected not in description:
            raise ValueError(f"{path} must document header-only public API keys and unsafe credential aliases")


def validate_no_unsafe_credential_fields(schema_name: str, properties: dict[str, Any]) -> None:
    exposed = sorted(UNSAFE_PUBLIC_CREDENTIAL_FIELDS & set(properties))
    if exposed:
        raise ValueError(f"{schema_name} must not expose credential fields: {', '.join(exposed)}")
