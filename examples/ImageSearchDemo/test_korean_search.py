#!/usr/bin/env python3
import json
import urllib.request


BASE_URL = "http://localhost:8110"
QUERIES = [
    "\ub178\ub780\uc0c9 \uc2e0\ubc1c",
    "\ud30c\ub780 \uc6b4\ub3d9\ud654",
    "\uac80\uc740\uc0c9 \ubca8\ud2b8",
    "\uac00\uc8fd \uc2dc\uacc4",
    "\ub0a8\uc131 \uce90\uc8fc\uc5bc \uac00\ubc29",
]


def post_json(path, payload, timeout=900):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search_text(query, limit=5):
    return post_json("/api/search", {"q": query, "limit": limit, "imageQuery": False})


def search_image(image_url, limit=5):
    return post_json("/api/search", {"q": image_url, "limit": limit, "imageQuery": True})


def print_hits(title, result):
    print(title)
    print(
        "  field={field} total={total}ms embedding={embedding}ms marqo={marqo}ms".format(
            field=result.get("vectorField"),
            total=result.get("ms"),
            embedding=result.get("vectorMs"),
            marqo=result.get("searchMs"),
        )
    )
    for hit in result["result"].get("hits", [])[:5]:
        print(
            "  {score:.4f} | {title} | {category} | {caption}".format(
                score=hit.get("_score", 0.0),
                title=hit.get("title"),
                category=hit.get("category"),
                caption=hit.get("caption"),
            )
        )
    print()


def main():
    first_image_url = None
    for query in QUERIES:
        result = search_text(query)
        print_hits(f"TEXT QUERY: {query}", result)
        if first_image_url is None and result["result"].get("hits"):
            first_image_url = result["result"]["hits"][0].get("image_url")

    if first_image_url:
        result = search_image(first_image_url)
        print_hits(f"IMAGE QUERY: {first_image_url}", result)


if __name__ == "__main__":
    main()
