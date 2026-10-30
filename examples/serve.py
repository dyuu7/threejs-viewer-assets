"""Local example only: serve assets from the installed package on loopback HTTP."""

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import unquote, urlsplit

import threejs_viewer_assets

EXAMPLES = Path(__file__).resolve().parent
ASSETS = files("threejs_viewer_assets").joinpath("assets")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        if path == "/":
            resource = EXAMPLES / "offline_demo.html"
        elif path in {"/demo.js", "/triangle.gltf"}:
            resource = EXAMPLES / path.lstrip("/")
        elif path.startswith("/assets/"):
            parts = path[len("/assets/") :].split("/")
            if any(part in {"", ".", ".."} or "\\" in part for part in parts):
                self.send_error(400)
                return
            resource = ASSETS
            for part in parts:
                resource = resource.joinpath(part)
        else:
            self.send_error(404)
            return
        if not resource.is_file():
            self.send_error(404)
            return
        body = resource.read_bytes()
        content_type = (
            "text/javascript" if path.endswith(".js") else mimetypes.guess_type(resource.name)[0]
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # Revalidate public assets without Chromium's no-store stream failure:
        # https://github.com/microsoft/playwright/issues/42742
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        manifest = json.loads(ASSETS.joinpath("manifest.json").read_text(encoding="utf-8"))
        print(
            json.dumps(
                {
                    "url": f"http://127.0.0.1:{server.server_port}",
                    "package_version": threejs_viewer_assets.__version__,
                    "upstream_version": manifest["upstream"]["version"],
                }
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
