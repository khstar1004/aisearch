param(
    [int]$Port = 8081,
    [string]$MarqoUrl = "http://127.0.0.1:8110/marqo",
    [string]$QwenUrl = "http://127.0.0.1:8111",
    [string]$IndexName = "haeorum-products-1800",
    [string]$ProductCsv = "",
    [string]$QueryEmbeddingCache = "",
    [string]$CorsOrigins = "http://127.0.0.1:3000"
)

$ErrorActionPreference = "Stop"

Write-Warning "Legacy Qwen-only demo script입니다. 현재 반입/테스트 기준은 scripts\start_jclgift_gemini_demo.ps1 또는 compose-haeorum-gemini.yaml 입니다."

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $ProductCsv) {
    $ProductCsv = Join-Path $root "logs\jclgift-products-1800-supported.csv"
}
if (-not $QueryEmbeddingCache) {
    $QueryEmbeddingCache = Join-Path $root "logs\jclgift-query-embeddings-1800.compact.json.gz"
}
$env:HAEORUM_ENV = "development"
$env:HAEORUM_SEARCH_ENGINE = "marqo"
$env:MARQO_URL = $MarqoUrl
$env:HAEORUM_EMBEDDING_BACKEND = "qwen"
$env:HAEORUM_QWEN_EMBEDDING_URL = $QwenUrl
$env:HAEORUM_QWEN_EMBEDDING_DIMENSIONS = "2048"
$env:HAEORUM_QWEN_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
$env:HAEORUM_INDEX_NAME = $IndexName
$env:HAEORUM_PRODUCT_CSV = $ProductCsv
if (Test-Path $QueryEmbeddingCache) {
    $env:HAEORUM_QWEN_QUERY_EMBEDDING_CACHE = $QueryEmbeddingCache
}
$env:HAEORUM_MALL_CONFIG_PATH = Join-Path $root "sample_malls.json"
$env:HAEORUM_QUERY_SYNONYM_PATH = Join-Path $root "sample_query_synonyms.json"
$env:HAEORUM_ADMIN_API_KEY = "dev-admin-key"
$env:HAEORUM_CORS_ORIGINS = $CorsOrigins
$env:HAEORUM_CACHE_TTL_SECONDS = "300"
$env:HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_IMAGE_SEARCH_MAX_CONCURRENCY = "30"
$env:HAEORUM_IMAGE_SEARCH_QUEUE_TIMEOUT_SECONDS = "60"

uv run `
    --directory $root `
    --with fastapi `
    --with uvicorn `
    --with pydantic `
    --with python-multipart `
    --with pillow `
    --with redis `
    --with psutil `
    python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
