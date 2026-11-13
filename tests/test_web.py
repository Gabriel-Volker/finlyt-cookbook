import base64
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
from PIL import Image

from cookbook_ocr.raster import run_pdf_task
from cookbook_ocr.web import CookbookHandler


class RequestSocket:
    def __init__(self, request):
        self.request = request
        self.response = bytearray()

    def makefile(self, mode, buffering):
        return BytesIO(self.request)

    def sendall(self, data):
        self.response.extend(data)


class WebTests(unittest.TestCase):
    def request(self, method, path, body=None, headers=None):
        body = body.encode("utf-8") if isinstance(body, str) else body or b""
        headers = {"Host": "127.0.0.1:8000", "Content-Length": len(body), **(headers or {})}
        lines = [f"{method} {path} HTTP/1.0", *(f"{key}: {value}" for key, value in headers.items()), "", ""]
        socket = RequestSocket("\r\n".join(lines).encode("ascii") + body)
        server = SimpleNamespace(server_address=("127.0.0.1", 8000))
        CookbookHandler(socket, ("127.0.0.1", 10000), server)
        header, data = bytes(socket.response).split(b"\r\n\r\n", 1)
        status, *response_headers = header.decode("ascii").split("\r\n")
        content_type = next(line.split(": ", 1)[1] for line in response_headers if line.startswith("Content-Type:"))
        return int(status.split()[1]), content_type, data

    def test_serve_assets_without_exposing_files_above_asset_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            assets = parent / "web_assets"
            assets.mkdir()
            (assets / "index.html").write_text("Cookbook workspace", encoding="utf-8")
            (assets / "fonts").mkdir()
            (assets / "fonts" / "book.woff2").write_bytes(b"font")
            (parent / ".env").write_text("API_KEY=secret", encoding="utf-8")
            with patch("cookbook_ocr.web.ASSETS", assets):
                self.assertEqual(self.request("GET", "/")[::2], (200, b"Cookbook workspace"))
                self.assertEqual(self.request("GET", "/assets/fonts/book.woff2"), (200, "font/woff2", b"font"))
                self.assertEqual(self.request("GET", "/assets/%2e%2e/.env")[0], 404)

    def test_preview_serves_the_requested_pdf_page(self):
        with pymupdf.open() as pdf:
            pdf.new_page(width=200, height=400)
            pdf.new_page(width=600, height=300)
            document = pdf.tobytes()
        status, _, response = self.request("POST", "/api/preview?filename=Recipe.pdf&page=2", document)
        self.assertEqual(status, 200)
        preview = json.loads(response)
        self.assertEqual(preview["page_count"], 2)
        kind, image_data = preview["image"].split(",", 1)
        self.assertEqual(kind, "data:image/png;base64")
        with Image.open(BytesIO(base64.b64decode(image_data))) as image:
            self.assertEqual(image.size, (1200, 600))

    def test_pdf_tasks_from_distinct_requests_share_one_worker(self):
        with ThreadPoolExecutor(max_workers=2) as requests:
            callers_ready = threading.Barrier(2)

            def request():
                callers_ready.wait(timeout=3)
                return threading.get_ident(), run_pdf_task(threading.get_ident)

            first = requests.submit(request)
            second = requests.submit(request)
            first_caller, first_worker = first.result(timeout=3)
            second_caller, second_worker = second.result(timeout=3)
        self.assertNotEqual(first_caller, second_caller)
        self.assertNotIn(first_worker, (first_caller, second_caller))
        self.assertEqual(first_worker, second_worker)

    def test_extract_rejects_cross_origin_upload_before_invoking_models(self):
        with patch("cookbook_ocr.web.run_pipeline") as run_pipeline:
            status, _, _ = self.request(
                "POST", "/api/extract?filename=Recipe.pdf", b"%PDF-1.4", {"Origin": "https://example.com"},
            )
        self.assertEqual(status, 403)
        run_pipeline.assert_not_called()

    def test_adapt_reuses_recipe_and_requires_a_baseline_when_missing(self):
        title = {"value": "Tea", "status": "stated", "evidence": None, "review_item_ids": []}
        servings = {"value": None, "status": "missing", "evidence": None, "review_item_ids": []}
        recipe = {"title": title, "servings": servings, "ingredients": [], "steps": [], "tools": []}
        report = {"recipe": recipe, "status": "no_discrepancies"}
        data = {"report": report, "servings": 6, "vegan": False}
        headers = {"Content-Type": "application/json"}
        status, _, response = self.request("POST", "/api/adapt", json.dumps(data), headers)
        self.assertEqual(status, 400)
        self.assertIn("no confirmed serving count", json.loads(response)["error"])

        data["original_servings"] = 4
        status, _, response = self.request("POST", "/api/adapt", json.dumps(data), headers)
        self.assertEqual(status, 200)
        adapted = json.loads(response)
        self.assertEqual(adapted["recipe"], recipe)
        self.assertEqual(adapted["adaptation"]["scale"]["factor"], "3/2")
        self.assertEqual(adapted["adaptation"]["recipe"]["servings"]["value"], "6")
