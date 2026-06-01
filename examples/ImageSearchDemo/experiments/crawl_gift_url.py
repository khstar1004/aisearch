#!/usr/bin/env python3
import argparse
import csv
import hashlib
import html
import pathlib
import re
import time
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://gift.url.kr/"
DEFAULT_OUTPUT = pathlib.Path(__file__).resolve().parent / "data" / "gift_url_products.csv"
USER_AGENT = "MarqoPromoSearchExperiment/0.1"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def parse_products(page_html, page_url):
    products = []
    for block in re.findall(
        r'<div class="product-card">(.*?)</div>\s*</div>\s*</div>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        image_match = re.search(r'<img[^>]+src="([^"]+)"[^>]+alt="([^"]*)"', block, re.IGNORECASE)
        title_match = re.search(r'<h3 class="product-title">(.*?)</h3>', block, re.IGNORECASE | re.DOTALL)
        category_match = re.search(r'class="product-category-badge"[^>]*>(.*?)</a>', block, re.IGNORECASE | re.DOTALL)
        link_match = re.search(r'<a\s+href="([^"]+)"[^>]*class="product-link"', block, re.IGNORECASE)
        price_match = re.search(r"가격:\s*</span>\s*<span>(.*?)</span>", block, re.IGNORECASE | re.DOTALL)

        if not image_match or not title_match or not category_match:
            continue

        image_url = urllib.parse.urljoin(page_url, html.unescape(image_match.group(1)))
        title = clean_text(title_match.group(1)) or clean_text(image_match.group(2))
        category = clean_text(category_match.group(1))
        source_url = urllib.parse.urljoin(page_url, html.unescape(link_match.group(1))) if link_match else page_url
        price = clean_text(price_match.group(1)) if price_match else ""
        product_id = "gift-url-" + hashlib.sha1(f"{source_url}|{image_url}".encode("utf-8")).hexdigest()[:16]

        products.append(
            {
                "id": product_id,
                "title": title,
                "category": category,
                "tags": make_tags(title, category),
                "image_url": image_url,
                "source_url": source_url,
                "price": price,
            }
        )
    return products


def make_tags(title, category):
    words = [category]
    for token in re.split(r"[\s,/()\-+]+", title):
        token = token.strip()
        if len(token) >= 2:
            words.append(token)
    return " ".join(dict.fromkeys(words))


def crawl(base_url, pages, max_products, delay):
    seen = set()
    products = []
    for page in range(1, pages + 1):
        page_url = base_url if page == 1 else f"{base_url.rstrip('/')}/?page={page}"
        page_products = parse_products(fetch(page_url), page_url)
        for product in page_products:
            dedupe_key = product["image_url"]
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            products.append(product)
            if len(products) >= max_products:
                return products
        if delay:
            time.sleep(delay)
    return products


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["id", "title", "category", "tags", "image_url", "source_url", "price"],
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--max-products", type=int, default=80)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    products = crawl(args.base_url, args.pages, args.max_products, args.delay)
    output = pathlib.Path(args.output)
    write_csv(output, products)
    print(f"wrote {len(products)} products to {output}")
    for product in products[:5]:
        print(f"- {product['category']} | {product['title']} | {product['image_url']}")


if __name__ == "__main__":
    main()
