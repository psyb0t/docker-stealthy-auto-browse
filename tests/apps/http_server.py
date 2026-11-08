#!/usr/bin/env python3
"""HTTP server for testing. Serves static files and handles uploads."""

import cgi
import json
import mimetypes
import os
import socketserver
import sys
from http.server import BaseHTTPRequestHandler

ROOT_DIR = "/srv"
UPLOAD_DIR = "/tmp/uploads"

MIME_FALLBACK = "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in (
            "/cookie-test/create",
            "/cookie-test/update",
            "/cookie-test/delete",
        ):
            value = "created" if self.path.endswith("create") else "updated"
            suffix = "; Max-Age=0" if self.path.endswith("delete") else ""
            body = b"<!doctype html><title>Cookie fixture</title><p>Cookie response</p>"
            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                f"http_session={value}; Path=/; HttpOnly; SameSite=Lax{suffix}",
            )
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # /referer — echo back the Referer header as JSON
        if self.path == "/referer":
            referer = self.headers.get("Referer", "")
            body = json.dumps({"referer": referer}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # /uploads/<name> — serve uploaded file back
        if self.path.startswith("/uploads/"):
            fname = os.path.basename(self.path)
            fpath = os.path.join(UPLOAD_DIR, fname)
            if not os.path.isfile(fpath):
                self.send_error(404, "Not found")
                return
            self._serve_file(fpath)
            return

        # Static file serving from ROOT_DIR
        path = self.path.split("?")[0].lstrip("/")
        if not path:
            path = "index.html"
        fpath = os.path.join(ROOT_DIR, path)

        if not os.path.isfile(fpath):
            self.send_error(404, "Not found")
            return
        self._serve_file(fpath)

    def do_POST(self):
        if self.path != "/upload":
            self.send_error(404, "Not found")
            return

        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )

        uploaded = form["file"]
        if not uploaded.filename:
            self.send_error(400, "No file uploaded")
            return

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        dest = os.path.join(UPLOAD_DIR, os.path.basename(uploaded.filename))
        with open(dest, "wb") as f:
            f.write(uploaded.file.read())

        result = {
            "filename": uploaded.filename,
            "size": os.path.getsize(dest),
            "path": dest,
        }
        print(f"UPLOAD {uploaded.filename} ({result['size']} bytes)", flush=True)

        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, fpath):
        mime, _ = mimetypes.guess_type(fpath)
        if not mime:
            mime = MIME_FALLBACK
        with open(fpath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    if len(sys.argv) > 2:
        ROOT_DIR = sys.argv[2]
    srv = socketserver.TCPServer(("0.0.0.0", port), Handler)
    print(f"SERVER_READY {port} root={ROOT_DIR}", flush=True)
    srv.serve_forever()
