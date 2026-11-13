import base64
import json
import tempfile
import threading
import unittest
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf
from PIL import Image

from cookbook_ocr.raster import run_pdf_task
from cookbook_ocr.scaling_plan import validate_scaling_plan
from cookbook_ocr.translation import TranslationError
from cookbook_ocr.web import CookbookHandler
from test_adaptation import field, ingredient, recipe, step, vegan_plan


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

    def test_output_defaults_to_english_and_preserves_the_source_recipe(self):
        recipe = {"title": {"value": "Sopa", "evidence": {"quote": "Sopa"}}}
        english = {"title": {"value": "Soup", "evidence": {"quote": "Sopa"}}}
        headers = {"Content-Type": "application/json"}
        with patch("cookbook_ocr.web.load_openai_key", return_value="test"), patch(
            "cookbook_ocr.web.translate_output", return_value={"recipe": english, "texts": {"reason": "Missing."}},
        ) as translate:
            data = {"recipe": recipe, "texts": {"reason": "Ausstehend."}}
            status, _, response = self.request("POST", "/api/output", json.dumps(data), headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"output": {"language": "english", "recipe": english,
                                                            "texts": {"reason": "Missing."}}})
        self.assertEqual(translate.call_args.args[1:], (recipe, data["texts"]))
        self.assertEqual(translate.call_args.kwargs["language"], "english")

    def test_original_and_known_english_outputs_need_no_model(self):
        recipe = {"title": {"value": "Tea"}}
        headers = {"Content-Type": "application/json"}
        options = (("original", False), ("english", True))
        with patch("cookbook_ocr.web.load_openai_key") as load_key:
            for language, source_is_english in options:
                with self.subTest(language=language):
                    data = {"recipe": recipe, "output_language": language, "source_is_english": source_is_english}
                    status, _, response = self.request("POST", "/api/output", json.dumps(data), headers)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(response), {"output": {"language": language, "recipe": recipe, "texts": {}}})
        load_key.assert_not_called()

    def test_output_translates_notes_into_the_original_recipe_language(self):
        source = {"title": {"value": "Suppe"}}
        final = {"title": {"value": "Vegane Suppe"}}
        headers = {"Content-Type": "application/json"}
        data = {"recipe": final, "source_recipe": source, "output_language": "original", "texts": {"reason": "Missing."}}
        localized = {"recipe": final, "texts": {"reason": "Fehlt."}}
        with patch("cookbook_ocr.web.load_openai_key", return_value="test"), patch(
            "cookbook_ocr.web.translate_output", return_value=localized,
        ) as translate:
            status, _, response = self.request("POST", "/api/output", json.dumps(data), headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"output": {"language": "original", **localized}})
        self.assertEqual(translate.call_args.kwargs, {"language": "original", "source_recipe": source})

    def test_failed_translation_is_not_returned_as_english(self):
        headers = {"Content-Type": "application/json"}
        data = {"recipe": {"title": {"value": "Sopa"}}}
        with patch("cookbook_ocr.web.load_openai_key", return_value="test"), patch(
            "cookbook_ocr.web.translate_output", side_effect=TranslationError("Could not verify the translation."),
        ):
            status, _, response = self.request("POST", "/api/output", json.dumps(data), headers)
        self.assertEqual(status, 502)
        self.assertEqual(json.loads(response), {"error": "Could not verify the translation."})

    def test_output_rejects_unsupported_languages_before_calling_luna(self):
        data = {"recipe": {"title": {"value": "Tea"}}, "output_language": "spanish"}
        with patch("cookbook_ocr.web.load_openai_key") as load_key:
            status, _, _ = self.request("POST", "/api/output", json.dumps(data), {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        load_key.assert_not_called()

    def test_output_reuses_validated_templates_for_servings_vegan_and_language_switches(self):
        source = recipe("4", [ingredient("butter", "Butter", "10", "g")],
                        [step("10 g Butter schmelzen.", "butter")])
        source["title"] = field("Suppe")
        plan = vegan_plan(source, [
            ("/ingredients/0/name", "Butter", "Margarine"),
            ("/steps/0/instruction", "10 g Butter schmelzen.", "10 g Margarine schmelzen."),
        ])
        scaled_vegan = deepcopy(source)
        scaled_vegan["ingredients"][0]["name"] = field("Margarine", "derived")
        scaled_vegan["ingredients"][0]["quantity"] = field("20", "derived")
        scaled_vegan["steps"][0]["instruction"] = field("20 g Margarine schmelzen.", "derived")
        model = Mock()
        known = {
            "Suppe": "Soup", "Butter": "Butter", "Margarine": "Margarine",
            "[MEASURE_A] Butter schmelzen.": "Melt [MEASURE_A] of butter.",
            "[MEASURE_A] Margarine schmelzen.": "Melt [MEASURE_A] of margarine.",
            "Check the quantity.": "Check the quantity.", "Menge prüfen.": "Check the amount.",
        }

        def respond(**payload):
            request = json.loads(payload["input"][1]["content"])
            return json.dumps({"translations": [
                {"path": item["path"], "text": known.get(item["text"], item["text"]) if request["language"] == "english"
                 else "Menge prüfen."}
                for item in request["fields"]
            ]})

        model.respond.side_effect = respond

        def output(current, language="english", **extra):
            data = {"recipe": current, "output_language": language, "translation_session": "cache-test", **extra}
            status, _, body = self.request("POST", "/api/output", json.dumps(data), {"Content-Type": "application/json"})
            self.assertEqual(status, 200, body)
            return json.loads(body)["output"]

        with patch("cookbook_ocr.web.load_openai_key", return_value="test"), patch(
            "cookbook_ocr.web.OpenAIClient", return_value=model,
        ), patch("cookbook_ocr.web._TRANSLATION_CACHE", OrderedDict()):
            first = output(source, texts={"review": "Menge prüfen."}, vegan_plan=plan,
                           prefetch_texts=["Check the quantity."])
            self.assertEqual(first["texts"], {"review": "Check the amount."})
            self.assertEqual(model.respond.call_count, 1)
            adapted = output(scaled_vegan, source_recipe=source, texts={"adapted": "Check the quantity."})
            self.assertEqual(adapted["recipe"]["steps"][0]["instruction"]["value"], "Melt 20 g of margarine.")
            self.assertEqual(adapted["texts"], {"adapted": "Check the quantity."})
            self.assertEqual(model.respond.call_count, 1)

            original = output(scaled_vegan, "original", source_recipe=source, texts={"adapted": "Check the quantity."})
            self.assertEqual(original["recipe"], scaled_vegan)
            self.assertEqual(original["texts"], {"adapted": "Menge prüfen."})
            self.assertEqual(model.respond.call_count, 2)
            self.assertEqual(output(scaled_vegan, source_recipe=source)["recipe"], adapted["recipe"])
            self.assertEqual(model.respond.call_count, 2)
            self.assertEqual([json.loads(call.kwargs["input"][1]["content"])["language"]
                              for call in model.respond.call_args_list], ["english", "original"])

    def test_prepare_both_languages_then_scale_with_no_provider(self):
        source = recipe("1", [ingredient("vinegar", "Essig", "½", "l"), ingredient("water", "Wasser", "½", "l")],
                        [step("½ l Essig und ½ l Wasser kochen.", "vinegar", "water")])
        source["title"] = field("Sud")
        rules = [{"ingredient_id": item["id"], "mode": "proportional", "reason": "Adjust the measured amount.",
                  "batch_servings": None, "evidence": []} for item in source["ingredients"]]
        report = {"recipe": source, "scaling_plan": {"original": validate_scaling_plan(source, {"rules": rules})}}
        context = {"translation_session": "prepared-bilingual", "texts": {"notice": "Check the amounts."}}
        headers = {"Content-Type": "application/json"}
        model = Mock()
        translations = {
            "Sud": "Marinade", "Essig": "vinegar", "Wasser": "water",
            "[MEASURE_A] Essig und [MEASURE_B] Wasser kochen.": "Boil [MEASURE_A] of vinegar and [MEASURE_B] of water.",
        }

        def respond(**payload):
            request = json.loads(payload["input"][1]["content"])
            return json.dumps({"translations": [
                {"path": item["path"], "text": translations.get(item["text"], item["text"])
                 if request["language"] == "english" else item["text"].replace("Check the amounts.", "Mengen prüfen.")}
                for item in request["fields"]
            ]})

        model.respond.side_effect = respond
        with patch("cookbook_ocr.web._TRANSLATION_CACHE", OrderedDict()), patch(
            "cookbook_ocr.web.load_openai_key", return_value="test",
        ) as load_key, patch("cookbook_ocr.web.OpenAIClient", return_value=model):
            status, _, body = self.request("POST", "/api/prepare-output", json.dumps({
                **context, "recipe": source, "can_scale": True,
            }), headers)
            self.assertEqual(status, 200, body)
            prepared = json.loads(body)
            self.assertEqual(set(prepared["outputs"]), {"english", "original"})
            self.assertEqual(prepared["output_errors"], {})
            self.assertEqual(prepared["outputs"]["original"]["texts"]["notice"], "Mengen prüfen.")
            self.assertEqual(model.respond.call_count, 2)
            load_key.reset_mock()
            model.respond.reset_mock(side_effect=True)
            model.respond.side_effect = AssertionError("Changing servings must not call the model.")
            for servings, amount in ((5, "2½"), (6, "3")):
                status, _, body = self.request("POST", "/api/adapt", json.dumps({
                    **context, "report": report, "servings": servings,
                }), headers)
                self.assertEqual(status, 200, body)
                result = json.loads(body)
                self.assertEqual(result["output_errors"], {})
                for language, unit, name in (("english", "L", "of vinegar"), ("original", "l", "Essig")):
                    output = result["outputs"][language]["recipe"]
                    self.assertEqual(output["ingredients"][0]["quantity"]["value"], amount)
                    self.assertIn(f"{amount} {unit} {name}", output["steps"][0]["instruction"]["value"])
            load_key.assert_not_called()
            model.respond.assert_not_called()

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
            {"path": "/steps/0/instruction", "action": "correct",
             "value": report["recipe"]["steps"][0]["instruction"]["value"].replace("1/2 l vinegar", "1/4 l vinegar")},
            {"path": "/servings", "action": "confirm", "value": "4"},
            {"path": f"/ingredients/{roast_index}/name", "action": "confirm", "value": "beef roast"},
            {"path": "/title", "action": "correct", "value": "Reviewed Sauerbraten"},
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
        self.assertEqual(review["recipe"]["title"]["value"], "Reviewed Sauerbraten")
        self.assertEqual(review["recipe"]["title"]["evidence"], original["recipe"]["title"]["evidence"])

        report["review"] = review
        report["vegan_plan"] = json.loads(response)["vegan_plan"]
        report["scaling_plan"] = {"original": validate_scaling_plan(review["recipe"], {"rules": [
            {"ingredient_id": item["id"], "mode": "proportional", "reason": "Measured ingredient.",
             "batch_servings": None, "evidence": []}
            for item in review["recipe"]["ingredients"]
        ]})}
        status, _, response = self.request(
            "POST", "/api/adapt", json.dumps({"report": report, "servings": 8, "vegan": False}), headers,
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
        self.assertEqual(result["recipe"]["ingredients"][roast_index]["name"]["value"], "beef roast")

        for invalid in ({"path": "/unknown", "action": "correct", "value": "Tea"},
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
            verify_vegan.assert_not_called()
            self.assertEqual(json.loads(response)["scaling_plan"], report["scaling_plan"])
            self.assertEqual(refresh_scaling.call_args.args[1:3], (review["recipe"], report["vegan_plan"]))

    def test_adapt_rejects_unverified_vegan_plan_with_reason_and_no_adaptation(self):
        assets = Path(__file__).resolve().parents[1] / "cookbook_ocr" / "web_assets"
        report = json.loads((assets / "example.json").read_text(encoding="utf-8"))
        report["vegan_plan"].update(
            status="needs_review", reason_code="insufficient_information", changes=[],
            reason="The recipe does not identify the animal-derived setting agent.", review_items=[],
        )
        report["vegan_plan"].pop("verification", None)
        headers = {"Content-Type": "application/json"}
        data = {"report": report, "vegan": True}
        status, _, response = self.request("POST", "/api/adapt", json.dumps(data), headers)
        self.assertEqual(status, 422)
        result = json.loads(response)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["reason_code"], "insufficient_information")
        self.assertIn("setting agent", result["error"])
        self.assertNotIn("adaptation", result)

        report["vegan_plan"].update(status="ready", reason_code=None, reason=None, review_items=[])
        status, _, response = self.request("POST", "/api/adapt", json.dumps(data), headers)
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(response)["reason_code"], "verification_unavailable")

    def test_review_saves_corrections_without_waiting_for_adaptation_providers(self):
        source = recipe("4", [ingredient("sugar", "sugar", "1", "cup")], [step("Mix the sugar.", "sugar")])
        data = {
            "report": {"recipe": source, "vegan_plan": None, "scaling_plan": None},
            "decisions": [{"path": "/ingredients/0/quantity", "action": "correct", "value": "2"}],
            "refresh_plans": False,
        }
        headers = {"Content-Type": "application/json"}
        with patch.object(CookbookHandler, "_refresh_plan") as vegan, patch.object(CookbookHandler, "_refresh_scaling") as scale:
            status, _, body = self.request("POST", "/api/review", json.dumps(data), headers)
            self.assertEqual(status, 200)
            result = json.loads(body)
            self.assertEqual(set(result), {"review"})
            reviewed = result["review"]["recipe"]
            self.assertEqual(reviewed["ingredients"][0]["quantity"]["value"], "2")
            vegan.assert_not_called()
            scale.assert_not_called()

            vegan.return_value = {"status": "unavailable"}
            scale.return_value = {"original": {"status": "ready"}}
            status, _, body = self.request("POST", "/api/plans", json.dumps(data), headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"vegan_plan": vegan.return_value, "scaling_plan": scale.return_value})
            self.assertEqual(vegan.call_args.args[1], reviewed)
            self.assertEqual(scale.call_args.args[1], reviewed)

    def test_servings_correction_reuses_verified_vegan_plan_without_a_provider(self):
        source = recipe(None, [ingredient("oil", "olive oil")], [step("Add olive oil.", "oil")])
        plan = vegan_plan(source, [])
        data = {
            "report": {"recipe": source, "vegan_plan": plan, "plan_decisions": []},
            "decisions": [{"path": "/servings", "action": "correct", "value": "4"}],
        }
        with patch("cookbook_ocr.web.load_openai_key", side_effect=ValueError), patch(
            "cookbook_ocr.web.plan_vegan_recipe",
        ) as plan_vegan:
            status, _, body = self.request("POST", "/api/plans", json.dumps(data), {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        refreshed = json.loads(body)["vegan_plan"]
        self.assertEqual(refreshed["status"], "ready")
        self.assertNotEqual(refreshed["recipe_fingerprint"], plan["recipe_fingerprint"])
        self.assertEqual(refreshed["verification"], plan["verification"])
        plan_vegan.assert_not_called()

    def test_adapt_applies_verified_vegan_example_with_missing_amounts(self):
        assets = Path(__file__).resolve().parents[1] / "cookbook_ocr" / "web_assets"
        report = json.loads((assets / "example.json").read_text(encoding="utf-8"))
        request = {"report": report, "vegan": True, "servings": 6, "original_servings": 4}
        status, _, response = self.request("POST", "/api/adapt", json.dumps(request), {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        result = json.loads(response)["adaptation"]
        roast_index = next(i for i, item in enumerate(report["recipe"]["ingredients"]) if item["name"]["value"] == "beef roast")
        before = report["recipe"]["ingredients"][roast_index]
        roast = result["recipe"]["ingredients"][roast_index]
        self.assertNotEqual(roast["name"]["value"], before["name"]["value"])
        self.assertEqual(roast["quantity"], before["quantity"])
        self.assertIsNone(roast["quantity"]["value"])
        self.assertIn(f"/ingredients/{roast_index}/quantity", {item["path"] for item in result["review_items"]})
        self.assertEqual(result["scale"]["factor"], "3/2")
