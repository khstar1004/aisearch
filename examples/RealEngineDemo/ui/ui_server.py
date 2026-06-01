#!/usr/bin/env python3
import argparse
import http.server
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parent


class DemoHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, marqo_url: str, **kwargs):
        self.marqo_url = marqo_url.rstrip("/")
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/marqo/"):
            self.proxy()
            return
        super().do_GET()

    def do_POST(self):
        self.proxy()

    def do_DELETE(self):
        self.proxy()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def proxy(self):
        if not self.path.startswith("/marqo/"):
            self.send_error(404)
            return

        target_path = self.path[len("/marqo") :]
        target_url = self.marqo_url + target_path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        request = urllib.request.Request(target_url, data=body, method=self.command, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:
            payload = json.dumps({"message": str(exc), "type": "proxy_error"}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--marqo-url", default=os.environ.get("MARQO_URL", "http://localhost:8882"))
    args = parser.parse_args()

    def handler(*handler_args, **handler_kwargs):
        return DemoHandler(*handler_args, marqo_url=args.marqo_url, **handler_kwargs)

    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    print(f"UI: http://{args.host}:{args.port}", flush=True)
    print(f"Proxying Marqo API: {args.marqo_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
