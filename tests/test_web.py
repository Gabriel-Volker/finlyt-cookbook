import base64
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf
from PIL import Image

from cookbook_ocr.adaptation import adapt_recipe
from cookbook_ocr.raster import run_pdf_task
from cookbook_ocr.scaling_plan import validate_scaling_plan
from cookbook_ocr.vegan import validate_vegan_plan
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

    def test_review_keeps_ocr_evidence_and_adapt_uses_the_reviewed_values(self):
        assets = Path(__file__).resolve().parents[1] / "cookbook_ocr" / "web_assets"
        report = json.loads((assets / "example.json").read_text(encoding="utf-8"))
        quantity = report["recipe"]["ingredients"][0]["quantity"]
        self.assertEqual(quantity["value"], "1/2")
        report["transcriptions"]["B"] = report["transcriptions"]["B"].replace("1/2 l vinegar", "1/4 l vinegar")
        report["comparison"]["review_items"].append({
            "page": "1", "section": "ingredients", "kind": "conflict",
            "a_excerpt": "1/2 l vinegar", "b_excerpt": "1/4 l vinegar",
            "reason": "The vinegar amounts differ.",
        })
        quantity.update(value=None, status="needs_review", review_item_ids=[1])
        roast_index = next(
            index for index, ingredient in enumerate(report["recipe"]["ingredients"])
            if ingredient["name"]["value"] == "beef roast"
        )
        report["recipe"]["ingredients"][roast_index]["name"].update(value=None, status="needs_review")
        report["recipe"]["servings"].update(value=None, status="needs_review")
        report["status"] = "needs_review"
        original = deepcopy(report)
        decisions = [
            {"path": "/ingredients/0/quantity", "action": "correct", "value": "1/4"},
            {"path": "/servings", "action": "confirm", "value": "4"},
            {"path": f"/ingredients/{roast_index}/name", "action": "confirm", "value": "beef roast"},
        ]
        headers = {"Content-Type": "application/json"}
        status, _, response = self.request(
            "POST", "/api/review", json.dumps({"report": report, "decisions": decisions}), headers,
        )
        self.assertEqual(status, 200)
        review = json.loads(response)["review"]
        self.assertEqual(review["status"], "reviewed")
        self.assertEqual(review["unresolved_paths"], [])
        reviewed_quantity = review["recipe"]["ingredients"][0]["quantity"]
        self.assertEqual(reviewed_quantity["evidence"], quantity["evidence"])
        self.assertEqual(reviewed_quantity["review_item_ids"], [1])
        self.assertEqual(reviewed_quantity["status"], "reviewed")
        self.assertIsNone(review["decisions"][0]["before"])
        self.assertEqual(review["decisions"][0]["after"], "1/4")

        report["review"] = review
        report["vegan_plan"] = validate_vegan_plan(review["recipe"], {
            "changes": [{
                "path": f"/ingredients/{roast_index}/name", "before": "beef roast", "after": "seitan",
                "reason": "Use a firm plant protein for the roast.",
            }],
            "review_items": [],
        })
        vegan_recipe = adapt_recipe(review["recipe"], vegan=True, vegan_plan=report["vegan_plan"])["recipe"]
        report["scaling_plan"] = {"vegan": validate_scaling_plan(vegan_recipe, {"rules": [
            {"ingredient_id": item["id"], "mode": "proportional", "reason": "Measured ingredient.",
             "batch_servings": None, "evidence": []}
            for item in vegan_recipe["ingredients"]
        ]})}
        status, _, response = self.request(
            "POST", "/api/adapt", json.dumps({"report": report, "servings": 8, "vegan": True}), headers,
        )
        self.assertEqual(status, 200)
        adapted = json.loads(response)
        self.assertEqual(adapted["recipe"], original["recipe"])
        self.assertEqual(adapted["transcriptions"], original["transcriptions"])
        self.assertEqual(adapted["comparison"], original["comparison"])
        self.assertEqual(adapted["review"], review)
        result = adapted["adaptation"]
        self.assertEqual(result["scale"]["original_servings_source"], "review")
        self.assertEqual(result["scale"]["factor"], "2")
        vinegar = result["recipe"]["ingredients"][0]
        self.assertEqual((vinegar["quantity"]["value"], vinegar["unit"]["value"]), ("500", "ml"))
        self.assertEqual(result["recipe"]["ingredients"][roast_index]["name"]["value"], "seitan")

        for invalid in ({"path": "/title", "action": "correct", "value": "Tea"},
                        {"path": "/servings", "action": "confirm", "value": "0"}):
            with self.subTest(invalid=invalid):
                body = json.dumps({"report": original, "decisions": [invalid]})
                self.assertEqual(self.request("POST", "/api/review", body, headers)[0], 400)

        with patch("cookbook_ocr.web.load_openai_key", return_value="test"), patch(
            "cookbook_ocr.web.optional_gemini_client", return_value=None,
        ), patch("cookbook_ocr.web.verify_vegan_plan", return_value=report["vegan_plan"]) as verify_vegan, patch(
            "cookbook_ocr.web.plan_vegan_recipe", return_value=report["vegan_plan"],
        ) as plan_vegan, patch(
            "cookbook_ocr.web.refresh_scaling_plans", return_value=report["scaling_plan"],
        ) as refresh_scaling:
            original.pop("example", None)
            original["vegan_plan"] = None
            original["scaling_plan"] = None
            body = json.dumps({"report": original, "decisions": decisions})
            status, _, response = self.request("POST", "/api/review", body, headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(response)["vegan_plan"], report["vegan_plan"])
            self.assertEqual(plan_vegan.call_args.args[1], review["recipe"])
            verify_vegan.assert_called_once_with(None, review["recipe"], report["vegan_plan"])
            self.assertEqual(json.loads(response)["scaling_plan"], report["scaling_plan"])
            self.assertEqual(refresh_scaling.call_args.args[1:3], (review["recipe"], report["vegan_plan"]))
