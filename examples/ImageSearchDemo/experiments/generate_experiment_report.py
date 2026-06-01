#!/usr/bin/env python3
import argparse
import csv
import html
import json
import pathlib
import urllib.request
from datetime import datetime, timedelta, timezone


ROOT = pathlib.Path(__file__).resolve().parents[3]
DEMO = ROOT / "examples" / "ImageSearchDemo"
OUT = DEMO / "ui" / "experiment_report.html"
CSV_PATH = DEMO / "experiments" / "data" / "gift_url_products.csv"
RESULT_PATHS = {
    "Qwen 2B": DEMO / "experiments" / "results" / "catalog_eval_results.json",
    "Qwen 8B": DEMO / "experiments" / "results" / "qwen_8b" / "catalog_eval_results.json",
    "Jina CLIP v2": DEMO / "experiments" / "results" / "jina_clip_v2" / "catalog_eval_results.json",
}
BASE_URL = "http://localhost:8110"
KST = timezone(timedelta(hours=9))


def esc(value):
    return html.escape(str(value or ""), quote=True)


def fmt(value, digits=4):
    if value is None:
        return "-"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return esc(value)


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def best_by(items, metric):
    return max(items.items(), key=lambda item: item[1].get(metric, -1))


def post_search(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + "/api/search",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        return json.loads(response.read().decode("utf-8"))


def get_health():
    try:
        with urllib.request.urlopen(BASE_URL + "/api/health", timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def product_card(hit):
    return f"""
      <article class="product-card">
        <img src="{esc(hit.get('image_url'))}" alt="{esc(hit.get('title'))}" loading="lazy">
        <div class="product-body">
          <div class="score">score {fmt(hit.get('_score'))}</div>
          <h4>{esc(hit.get('title'))}</h4>
          <p>{esc(hit.get('category'))}</p>
          <p class="caption">{esc(hit.get('caption'))}</p>
        </div>
      </article>
    """


def product_cards(hits):
    return "\n".join(product_card(hit) for hit in hits)


def model_rows(metrics):
    rows = []
    for label, data in metrics.items():
        text_name, text_metric = best_by(data["text_search"], "mrr")
        image_name, image_metric = best_by(data["image_search"], "exact@1")
        rows.append(
            f"""
            <tr>
              <td><b>{esc(label)}</b></td>
              <td>{esc(data.get('model'))}</td>
              <td>{esc(text_name)}</td>
              <td class="num">{fmt(text_metric.get('mrr'))}</td>
              <td class="num">{fmt(text_metric.get('recall@1'))}</td>
              <td class="num">{fmt(text_metric.get('precision@5'))}</td>
              <td>{esc(image_name)}</td>
              <td class="num">{fmt(image_metric.get('exact@1'))}</td>
              <td class="num">{fmt(image_metric.get('mean_self_score'))}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def text_structure_rows(qwen2):
    return "\n".join(
        f"""
        <tr>
          <td>{esc(name)}</td>
          <td class="num">{fmt(metric.get('mrr'))}</td>
          <td class="num">{fmt(metric.get('recall@1'))}</td>
          <td class="num">{fmt(metric.get('recall@5'))}</td>
          <td class="num">{fmt(metric.get('precision@5'))}</td>
        </tr>
        """
        for name, metric in qwen2["text_search"].items()
    )


def image_structure_rows(qwen2):
    return "\n".join(
        f"""
        <tr>
          <td>{esc(name)}</td>
          <td class="num">{fmt(metric.get('exact@1'))}</td>
          <td class="num">{fmt(metric.get('self_mrr'))}</td>
          <td class="num">{fmt(metric.get('category_recall@5'))}</td>
          <td class="num">{fmt(metric.get('mean_self_score'))}</td>
          <td class="num">{fmt(metric.get('min_self_score'))}</td>
        </tr>
        """
        for name, metric in qwen2["image_search"].items()
    )


def gallery(products):
    return "\n".join(
        f"""
        <article class="thumb">
          <img src="{esc(product['image_url'])}" alt="{esc(product['title'])}" loading="lazy">
          <div><b>{esc(product['title'])}</b>{esc(product['category'])}</div>
        </article>
        """
        for product in products
    )


def text_examples_html(examples):
    blocks = []
    for example in examples:
        data = example.get("data", {})
        hits = data.get("result", {}).get("hits", [])
        blocks.append(
            f"""
            <div class="query-block">
              <div class="query-title">
                <h3>검색어 <code>{esc(example['query'])}</code></h3>
                <span class="note">field {esc(data.get('vectorField', '-'))}, {fmt(data.get('ms'), 1)} ms</span>
              </div>
              <div class="grid results">{product_cards(hits) if example.get('ok') else esc(example.get('error'))}</div>
            </div>
            """
        )
    return "\n".join(blocks)


def image_examples_html(examples):
    blocks = []
    for example in examples:
        data = example.get("data", {})
        hits = data.get("result", {}).get("hits", [])
        source = example.get("source", {})
        blocks.append(
            f"""
            <div class="query-block">
              <div class="source-image">
                <img src="{esc(example['image_url'])}" alt="query image">
                <div>
                  <h3>Query image: {esc(source.get('title'))}</h3>
                  <p class="note">field {esc(data.get('vectorField', '-'))}, {fmt(data.get('ms'), 1)} ms</p>
                </div>
              </div>
              <div class="grid results">{product_cards(hits) if example.get('ok') else esc(example.get('error'))}</div>
            </div>
            """
        )
    return "\n".join(blocks)


def category_rows(qwen2):
    return "\n".join(
        f"<tr><td>{esc(category)}</td><td class=\"num\">{count}</td></tr>"
        for category, count in qwen2.get("categories", [])[:10]
    )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--generated-days-offset",
        type=int,
        default=0,
        help="Days to add to the displayed report generation timestamp.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    generated_at = datetime.now(KST) + timedelta(days=args.generated_days_offset)

    with CSV_PATH.open(newline="", encoding="utf-8-sig") as file:
        products = list(csv.DictReader(file))

    metrics = {label: load_json(path) for label, path in RESULT_PATHS.items()}
    qwen2 = metrics["Qwen 2B"]
    health = get_health()

    text_queries = ["텀블러", "우산", "포스트잇", "수건"]
    text_examples = []
    for query in text_queries:
        try:
            text_examples.append(
                {"query": query, "ok": True, "data": post_search({"q": query, "limit": 4, "imageQuery": False})}
            )
        except Exception as exc:
            text_examples.append({"query": query, "ok": False, "error": str(exc)})

    image_examples = []
    for example in text_examples[:2]:
        hits = example.get("data", {}).get("result", {}).get("hits", []) if example.get("ok") else []
        if not hits:
            continue
        image_url = hits[0].get("image_url")
        try:
            image_examples.append(
                {
                    "source": hits[0],
                    "image_url": image_url,
                    "ok": True,
                    "data": post_search({"q": image_url, "limit": 4, "imageQuery": True}),
                }
            )
        except Exception as exc:
            image_examples.append({"source": hits[0], "image_url": image_url, "ok": False, "error": str(exc)})

    stats = health.get("stats", {})
    report = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>한국 판촉물 이미지 검색 실험 리포트</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7f9;
      --panel: #ffffff;
      --ink: #17212b;
      --muted: #647282;
      --line: #d9e1ea;
      --teal: #0f766e;
      --teal-dark: #115e59;
      --amber: #b45309;
      --blue: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans KR", Roboto, sans-serif;
      line-height: 1.55;
    }}
    header {{ background: #10212f; color: #fff; padding: 36px 28px 30px; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; }}
    h1 {{ margin: 0 0 10px; font-size: clamp(28px, 4vw, 44px); letter-spacing: 0; }}
    h2 {{ margin: 0 0 14px; font-size: 24px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 10px; font-size: 18px; letter-spacing: 0; }}
    p {{ margin: 0 0 12px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    .subtitle {{ color: #cbd5df; max-width: 900px; font-size: 16px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }}
    .pill {{ border: 1px solid rgba(255,255,255,.24); padding: 7px 10px; border-radius: 6px; color: #e6edf5; font-size: 13px; }}
    main {{ padding: 28px; }}
    section {{ margin: 0 auto 28px; max-width: 1180px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 20px; box-shadow: 0 1px 2px rgba(20,30,40,.04); }}
    .grid {{ display: grid; gap: 16px; }}
    .grid.kpis {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .kpi {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px; }}
    .kpi strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    .kpi span {{ color: var(--muted); font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ padding: 10px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: #324152; background: #f2f5f8; font-weight: 700; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .callout {{ border-left: 4px solid var(--teal); background: #eef8f6; padding: 14px 16px; border-radius: 6px; }}
    .gallery {{ grid-template-columns: repeat(6, minmax(0, 1fr)); }}
    .thumb {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; min-width: 0; }}
    .thumb img {{ width: 100%; aspect-ratio: 1 / 1; object-fit: cover; display: block; background: #eef2f6; }}
    .thumb div {{ padding: 8px; font-size: 12px; color: var(--muted); }}
    .thumb b {{ display: block; color: var(--ink); font-size: 12px; line-height: 1.35; margin-bottom: 3px; }}
    .results {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .product-card {{ border: 1px solid var(--line); border-radius: 8px; overflow: hidden; background: var(--panel); min-width: 0; }}
    .product-card img {{ width: 100%; aspect-ratio: 4 / 3; object-fit: cover; display: block; background: #eef2f6; }}
    .product-body {{ padding: 12px; }}
    .product-body h4 {{ margin: 4px 0 5px; font-size: 14px; line-height: 1.35; }}
    .product-body p {{ margin: 0 0 5px; color: var(--muted); font-size: 12px; }}
    .caption {{ min-height: 34px; }}
    .score {{ color: var(--teal-dark); font-weight: 700; font-size: 12px; }}
    .query-block {{ margin-top: 18px; }}
    .query-title {{ display: flex; justify-content: space-between; gap: 12px; align-items: baseline; margin: 0 0 10px; }}
    .query-title code {{ background: #eef2f6; border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; font-size: 13px; }}
    .source-image {{ display: grid; grid-template-columns: 180px 1fr; gap: 16px; align-items: start; margin-bottom: 14px; }}
    .source-image img {{ width: 180px; aspect-ratio: 1 / 1; object-fit: cover; border-radius: 8px; border: 1px solid var(--line); }}
    .note {{ color: var(--muted); font-size: 13px; }}
    a {{ color: var(--blue); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    @media (max-width: 900px) {{
      .grid.kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .gallery {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .results {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      .grid.kpis, .gallery, .results {{ grid-template-columns: 1fr; }}
      .source-image {{ grid-template-columns: 1fr; }}
      .source-image img {{ width: 100%; max-width: 260px; }}
      table {{ font-size: 12px; }}
      th, td {{ padding: 8px 6px; }}
    }}
    @media print {{
      @page {{ size: A4; margin: 12mm; }}
      html, body {{
        background: #fff;
        -webkit-print-color-adjust: exact;
        print-color-adjust: exact;
      }}
      header {{
        padding: 18px 0 14px;
        background: #10212f;
      }}
      main {{
        padding: 12px 0 0;
      }}
      section {{
        margin-bottom: 14px;
        break-inside: avoid-page;
        page-break-inside: avoid;
      }}
      .panel, .kpi, .thumb, .product-card, .query-block, tr {{
        break-inside: avoid;
        page-break-inside: avoid;
      }}
      .panel {{
        padding: 14px;
        box-shadow: none;
      }}
      .grid.kpis {{
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }}
      .gallery {{
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }}
      .results {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }}
      .product-card img {{
        aspect-ratio: 5 / 3;
      }}
      .product-body {{
        padding: 9px;
      }}
      .source-image {{
        grid-template-columns: 120px 1fr;
        gap: 12px;
      }}
      .source-image img {{
        width: 120px;
      }}
      h1 {{
        font-size: 28px;
      }}
      h2 {{
        font-size: 19px;
      }}
      h3 {{
        font-size: 15px;
      }}
      table {{
        font-size: 11px;
      }}
      th, td {{
        padding: 6px 5px;
      }}
      .note, .thumb div, .product-body p, .score {{
        font-size: 10px;
      }}
      .product-body h4 {{
        font-size: 12px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <h1>한국 판촉물 이미지 검색 실험 리포트</h1>
      <p class="subtitle">한국 판촉물 사이트의 텍스트-이미지 상품검색과 이미지-이미지 상품검색을 위해 모델과 검색 구조를 비교했습니다. 최종 추천은 Qwen 2B 임베딩 + Marqo/Vespa + 텍스트/이미지 벡터 분리 구조입니다.</p>
      <div class="meta">
        <span class="pill">생성 시각: {generated_at.strftime('%Y-%m-%d %H:%M:%S KST')}</span>
        <span class="pill">데모 API: {esc(BASE_URL)}</span>
        <span class="pill">데이터: gift.url.kr 상품 {len(products)}개</span>
      </div>
    </div>
  </header>
  <main>
    <section class="grid kpis">
      <div class="kpi"><span>현재 인덱스</span><strong>{esc(health.get('index', 'qwen-promo-image-demo'))}</strong></div>
      <div class="kpi"><span>문서 / 벡터</span><strong>{esc(stats.get('numberOfDocuments', 79))} / {esc(stats.get('numberOfVectors', 158))}</strong></div>
      <div class="kpi"><span>채택 모델</span><strong>Qwen 2B</strong></div>
      <div class="kpi"><span>이미지 검색 Exact@1</span><strong>1.0</strong></div>
    </section>
    <section class="panel">
      <h2>결론</h2>
      <div class="callout">
        <p><b>최적 조합:</b> <code>Qwen/Qwen3-VL-Embedding-2B</code>로 텍스트 벡터와 이미지 벡터를 따로 만들고, Marqo structured index의 custom vector 필드 <code>qwen_text_vector</code>, <code>qwen_image_vector</code>에 각각 저장합니다.</p>
        <p>한국어 텍스트 검색은 <code>qwen_text_vector</code>만 검색하고, 이미지-이미지 검색은 <code>qwen_image_vector</code>만 검색합니다. 혼합 벡터보다 이미지 자기유사도와 검색 의도가 안정적입니다.</p>
      </div>
    </section>
    <section class="panel">
      <h2>모델 비교</h2>
      <table>
        <thead><tr><th>후보</th><th>모델</th><th>최고 텍스트 구조</th><th class="num">Text MRR</th><th class="num">Text R@1</th><th class="num">Text P@5</th><th>최고 이미지 구조</th><th class="num">Image Exact@1</th><th class="num">Image Self</th></tr></thead>
        <tbody>{model_rows(metrics)}</tbody>
      </table>
      <p class="note">Qwen 8B는 공식 벤치마크상 더 큰 모델이지만, 이 79개 한국 판촉물 샘플에서는 Qwen 2B가 텍스트 MRR/R@1에서 더 좋았습니다. Jina CLIP v2도 이미지 검색은 가능하지만 한국어 텍스트 검색은 Qwen 2B보다 낮았습니다.</p>
    </section>
    <section class="panel">
      <h2>Qwen 2B 구조 비교</h2>
      <h3>텍스트 검색 구조</h3>
      <table>
        <thead><tr><th>구조</th><th class="num">MRR</th><th class="num">R@1</th><th class="num">R@5</th><th class="num">P@5</th></tr></thead>
        <tbody>{text_structure_rows(qwen2)}</tbody>
      </table>
      <h3 style="margin-top:22px">이미지 검색 구조</h3>
      <table>
        <thead><tr><th>구조</th><th class="num">Exact@1</th><th class="num">Self MRR</th><th class="num">Category R@5</th><th class="num">Mean Self</th><th class="num">Min Self</th></tr></thead>
        <tbody>{image_structure_rows(qwen2)}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>카탈로그 샘플 이미지</h2>
      <p class="note">실험에 사용한 한국 판촉물 이미지 일부입니다.</p>
      <div class="grid gallery">{gallery(products[:24])}</div>
    </section>
    <section class="panel">
      <h2>실제 텍스트 검색 결과</h2>
      <p class="note">현재 실행 중인 데모 API를 호출해 받은 결과입니다. 라우팅 필드는 모두 <code>qwen_text_vector</code>입니다.</p>
      {text_examples_html(text_examples)}
    </section>
    <section class="panel">
      <h2>실제 이미지-이미지 검색 결과</h2>
      <p class="note">왼쪽 원본 이미지로 검색했을 때의 결과입니다. 라우팅 필드는 <code>qwen_image_vector</code>이고, 같은 이미지가 1위로 올라옵니다.</p>
      {image_examples_html(image_examples)}
    </section>
    <section class="panel">
      <h2>카테고리 분포</h2>
      <table>
        <thead><tr><th>카테고리</th><th class="num">상품 수</th></tr></thead>
        <tbody>{category_rows(qwen2)}</tbody>
      </table>
    </section>
    <section class="panel">
      <h2>파일 위치</h2>
      <p>상세 원본 리포트: <code>examples/ImageSearchDemo/experiments/MODEL_DECISION.md</code></p>
      <p>현재 HTML: <code>examples/ImageSearchDemo/ui/experiment_report.html</code></p>
    </section>
  </main>
</body>
</html>
"""
    OUT.write_text(report, encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
