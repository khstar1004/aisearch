param(
    [int]$Port = 8120,
    [string]$MarqoUrl = "http://127.0.0.1:8122",
    [string]$GeminiUrl = "http://127.0.0.1:8098",
    [string]$GeminiProxyApiKey = $env:GEMINI_PROXY_API_KEY,
    [string]$IndexName = "haeorum-gemini-marqo-jclgift",
    [string]$ProductCsv = "",
    [string]$CorsOrigins = "http://127.0.0.1:3000"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
if (-not $ProductCsv) {
    $ProductCsv = Join-Path $root "logs\jclgift-products-1800-supported.csv"
}

$env:HAEORUM_ENV = "development"
$env:HAEORUM_SEARCH_ENGINE = "marqo"
$env:MARQO_URL = $MarqoUrl
$env:HAEORUM_EMBEDDING_BACKEND = "gemini"
$env:HAEORUM_GEMINI_EMBEDDING_URL = $GeminiUrl
if ($GeminiProxyApiKey) {
    $env:HAEORUM_GEMINI_EMBEDDING_PROXY_API_KEY = $GeminiProxyApiKey
}
$env:HAEORUM_GEMINI_EMBEDDING_DIMENSIONS = "1536"
$env:HAEORUM_GEMINI_MODEL = "gemini-embedding-2"
$env:HAEORUM_INDEX_NAME = $IndexName
$env:HAEORUM_PRODUCT_CSV = $ProductCsv
$env:HAEORUM_MALL_CONFIG_PATH = Join-Path $root "sample_malls.json"
$env:HAEORUM_QUERY_SYNONYM_PATH = Join-Path $root "sample_query_synonyms.json"
$env:HAEORUM_ADMIN_API_KEY = "dev-admin-key"
$env:HAEORUM_CORS_ORIGINS = $CorsOrigins
$env:HAEORUM_CACHE_TTL_SECONDS = "300"
$env:HAEORUM_SEARCH_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_MALL_SEARCH_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_IMAGE_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_MALL_IMAGE_RATE_LIMIT_PER_MINUTE = "100000"
$env:HAEORUM_GEMINI_QUERY_TIMEOUT_SECONDS = "30"
$env:HAEORUM_GEMINI_MIXED_QUERY_PARALLELISM = "2"
$env:HAEORUM_GEMINI_QUERY_RUNTIME_TEXT_CACHE_ENTRIES = "2048"
$env:HAEORUM_GEMINI_QUERY_RUNTIME_IMAGE_CACHE_ENTRIES = "512"
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
