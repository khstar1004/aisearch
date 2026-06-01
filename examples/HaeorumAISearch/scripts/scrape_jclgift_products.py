from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://www.jclgift.com"
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SEED_URLS = (
    "https://www.jclgift.com/_mobile/product_w/?a_code=J&b_code=JCN&depth2=Y",
    "https://www.jclgift.com/_mobile/product_w/?a_code=C&depth2=Y",
)
FIELDNAMES = [
    "product_id",
    "product_name",
    "price",
    "category_name",
    "main_image_url",
    "product_url",
    "status",
    "updated_at",
    "is_deleted",
    "display_yn",
    "mall_id",
    "description",
    "keywords",
    "image_tags",
]


def fetch_text(url: str, *, timeout: float = 25.0, retries: int = 2, delay: float = 0.2) -> str:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; HaeorumAISearchEvaluation/1.0)",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
            return data.decode("euc-kr", "ignore")
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(delay * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def clean_text(value: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def absolute_url(url: str, base_url: str) -> str:
    absolute = urllib.parse.urljoin(base_url, html.unescape(url).strip())
    parsed = urllib.parse.urlparse(absolute)
    return urllib.parse.urlunparse(parsed._replace(path=urllib.parse.quote(parsed.path, safe="/%")))


def normalized_category_url(url: str) -> str | None:
    absolute = absolute_url(url, BASE_URL)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.netloc.lower() != "www.jclgift.com":
        return None
    if not parsed.path.lower().startswith("/_mobile/product_w/"):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if not query.get("a_code"):
        return None
    if "product_view.asp" in parsed.path.lower() or "search_keyword.asp" in parsed.path.lower():
        return None
    if "b_code" not in {key.lower() for key in query} and "b_tcode" not in {key.lower() for key in query}:
        return None
    normalized_query: dict[str, str] = {}
    for key, values in query.items():
        lower = key.lower()
        if lower in {"a_code", "b_code", "b_tcode", "depth2"} and values:
            normalized_query[key] = values[0]
    if not normalized_query:
        return None
    path = "/_mobile/product_w/Default.asp"
    return urllib.parse.urlunparse(
        (
            "https",
            "www.jclgift.com",
            path,
            "",
            urllib.parse.urlencode(normalized_query),
            "",
        )
    )


def discover_category_urls(html_text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw_href in re.findall(r'href=["\']([^"\']+)["\']', html_text, flags=re.I):
        category_url = normalized_category_url(raw_href)
        if category_url and category_url not in seen:
            seen.add(category_url)
            urls.append(category_url)
    return urls


def category_name_from_page(html_text: str, fallback: str) -> str:
    titles = [
        clean_text(match)
        for match in re.findall(r'<div\s+class=["\']Title["\'][^>]*>(.*?)</div>', html_text, flags=re.I | re.S)
    ]
    for title in titles:
        if "카테고리" in title and ">" in title:
            return title.split(">")[-1].strip() or fallback
    return fallback


def parse_price(value: str) -> str:
    match = re.search(r"판매가\s*([0-9,]+)", clean_text(value))
    return match.group(1).replace(",", "") if match else ""


def parse_product_cards(html_text: str, page_url: str, category_name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a\s+href=["\'](?P<href>[^"\']*product_view\.asp\?p_idx=(?P<pidx>\d+)[^"\']*)["\'][^>]*>(?P<body>.*?)</a>',
        flags=re.I | re.S,
    )
    for match in pattern.finditer(html_text):
        body = match.group("body")
        title_match = re.search(r'<div\s+class=["\']ProductTitle["\'][^>]*>(.*?)</div>', body, flags=re.I | re.S)
        title = clean_text(title_match.group(1) if title_match else body)
        if not title or "기획전" in title or len(title) > 180:
            continue
        image_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', body, flags=re.I)
        image_url = absolute_url(image_match.group(1), page_url) if image_match else ""
        if not image_url.lower().startswith("https://"):
            continue
        image_path = urllib.parse.urlparse(image_url).path.lower()
        if not any(image_path.endswith(extension) for extension in SUPPORTED_IMAGE_EXTENSIONS):
            continue
        pidx = match.group("pidx")
        product_url = absolute_url(match.group("href"), page_url)
        keywords = ";".join(part for part in [category_name, title.replace(" ", ";")] if part)
        rows.append(
            {
                "product_id": f"JCL{pidx}",
                "product_name": title,
                "price": parse_price(body),
                "category_name": category_name,
                "main_image_url": image_url,
                "product_url": product_url,
                "status": "active",
                "updated_at": datetime.now(timezone.utc).date().isoformat() + "T00:00:00Z",
                "is_deleted": "false",
                "display_yn": "Y",
                "mall_id": "",
                "description": title,
                "keywords": keywords,
                "image_tags": keywords,
            }
        )
    return rows


def page_url(category_url: str, page: int) -> str:
    parsed = urllib.parse.urlparse(category_url)
    query = urllib.parse.parse_qs(parsed.query)
    query["page"] = [str(page)]
    query.setdefault("m_page_size", ["50"])
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def scrape(
    target: int,
    max_pages_per_category: int,
    sleep_seconds: float,
    max_new_per_category_page: int,
) -> dict[str, Any]:
    category_urls: list[str] = []
    seen_category_urls: set[str] = set()
    for seed_url in SEED_URLS:
        seed_html = fetch_text(seed_url)
        for category_url in discover_category_urls(seed_html):
            if category_url not in seen_category_urls:
                seen_category_urls.add(category_url)
                category_urls.append(category_url)

    products: dict[str, dict[str, str]] = {}
    page_reports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    stale_pages_by_category = {category_url: 0 for category_url in category_urls}
    for page in range(1, max_pages_per_category + 1):
        active_categories = 0
        for category_url in category_urls:
            if len(products) >= target:
                break
            if stale_pages_by_category.get(category_url, 0) >= 2:
                continue
            active_categories += 1
            fallback_category = urllib.parse.parse_qs(urllib.parse.urlparse(category_url).query).get("b_code", [""])[0]
            url = page_url(category_url, page)
            try:
                html_text = fetch_text(url)
            except RuntimeError as exc:
                errors.append({"url": url, "error": str(exc)})
                stale_pages_by_category[category_url] = 2
                continue
            category_name = category_name_from_page(html_text, fallback_category)
            rows = parse_product_cards(html_text, url, category_name)
            new_count = 0
            for row in rows:
                key = row["product_id"]
                if key not in products:
                    products[key] = row
                    new_count += 1
                    if new_count >= max_new_per_category_page:
                        break
            page_reports.append(
                {
                    "url": url,
                    "category_name": category_name,
                    "parsed": len(rows),
                    "new": new_count,
                    "total": len(products),
                }
            )
            if new_count == 0:
                stale_pages_by_category[category_url] = stale_pages_by_category.get(category_url, 0) + 1
            else:
                stale_pages_by_category[category_url] = 0
            time.sleep(sleep_seconds)
        if len(products) >= target or active_categories == 0:
            break

    selected = list(products.values())[:target]
    category_counts = Counter(row["category_name"] for row in selected)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://www.jclgift.com/_mobile/product_w/",
        "target": target,
        "collected": len(selected),
        "category_urls": len(category_urls),
        "pages_fetched": len(page_reports),
        "errors": errors[:20],
        "top_categories": [{"category": category, "count": count} for category, count in category_counts.most_common(20)],
        "rows": selected,
        "page_reports": page_reports,
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=9000)
    parser.add_argument("--max-pages-per-category", type=int, default=4)
    parser.add_argument("--max-new-per-category-page", type=int, default=25)
    parser.add_argument("--sleep-seconds", type=float, default=0.08)
    parser.add_argument("--output-csv", type=Path, default=ROOT / "logs" / "jclgift-products-9000-web.csv")
    parser.add_argument("--report-json", type=Path, default=ROOT / "reports" / "jclgift-products-9000-web-scrape.json")
    args = parser.parse_args()

    report = scrape(
        target=max(1, args.target),
        max_pages_per_category=max(1, args.max_pages_per_category),
        sleep_seconds=max(0.0, args.sleep_seconds),
        max_new_per_category_page=max(1, args.max_new_per_category_page),
    )
    write_csv(args.output_csv, report.pop("rows"))
    report["output_csv"] = str(args.output_csv)
    report["csv_rows"] = sum(1 for _ in args.output_csv.open("r", encoding="utf-8-sig")) - 1
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["collected", "category_urls", "pages_fetched", "output_csv", "top_categories"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
