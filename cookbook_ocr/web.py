"""Serve the local Cookbook workspace and its recipe API."""

import argparse
import base64
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from math import isfinite
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pymupdf
from PIL import Image, ImageOps

from .__main__ import load_api_keys, load_openai_key
from .adaptation import adapt_recipe
from .pipeline import UnreadableRecipeError, plan_vegan_recipe, run_pipeline
from .providers import GeminiClient, MIME_TYPES, OpenAIClient, ProviderError
from .raster import run_pdf_task
from .review import review_recipe
from .vegan import vegan_plan_matches


ASSETS = Path(__file__).with_name("web_assets")
MAX_UPLOAD_BYTES = 14_000_000
MAX_REPORT_BYTES = 2_000_000
PREVIEW_EDGE = 1200
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
STATIC_TYPES = {".js": "application/javascript", ".svg": "image/svg+xml", ".woff2": "font/woff2"}


class RequestError(ValueError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def _media_type(filename: str, document: bytes) -> str:
    mime_type = MIME_TYPES.get(Path(filename).suffix.lower())
    if mime_type is None:
        raise RequestError("Choose a PDF, PNG or JPEG file.", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
    signatures = {
        "application/pdf": b"%PDF-",
        "image/png": b"\x89PNG\r\n\x1a\n",
        "image/jpeg": b"\xff\xd8\xff",
    }
    signature = signatures[mime_type]
    matches = signature in document[:1024] if mime_type == "application/pdf" else document.startswith(signature)
    if not matches:
        raise RequestError("The file content does not match its extension.")
    return mime_type


def _preview_pdf(document: bytes, page_number: int) -> tuple[int, bytes]:
    with pymupdf.open(stream=document, filetype="pdf") as pdf:
        if pdf.needs_pass:
            raise RequestError("Unlock the PDF before opening its preview.")
        page_count = pdf.page_count
        if page_number > page_count:
            raise RequestError("Choose an existing page.")
        page = pdf[page_number - 1]
        dimensions = (page.rect.width, page.rect.height)
        if not all(isfinite(side) and side > 0 for side in dimensions):
            raise RequestError("The page has invalid dimensions.")
        scale = PREVIEW_EDGE / max(dimensions)
        png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
        return page_count, png


def _preview(document: bytes, mime_type: str, page_number: int) -> dict:
    if page_number < 1:
        raise RequestError("Choose an existing page.")
    try:
        if mime_type == "application/pdf":
            page_count, png = run_pdf_task(_preview_pdf, document, page_number)
        else:
            page_count = 1
            if page_number != 1:
                raise RequestError("Choose an existing page.")
            with Image.open(BytesIO(document)) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                png = buffer.getvalue()
    except (RuntimeError, OSError, Image.DecompressionBombError):
        raise RequestError("Could not open a preview of this file.") from None

    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return {"page_count": page_count, "image": data_url}


class CookbookHandler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def _body(self, maximum: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise RequestError("Send a request body with a content length.", HTTPStatus.LENGTH_REQUIRED) from None
        if length < 1:
            raise RequestError("The request body is empty.")
        if length > maximum:
            raise RequestError("The request is too large.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        body = self.rfile.read(length)
        if len(body) != length:
            raise RequestError("The request body is incomplete.")
        return body

    def _check_origin(self) -> None:
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        bound_host = self.server.server_address[0]
        if urlsplit(f"//{host}").hostname not in LOCAL_HOSTS | {bound_host}:
            raise RequestError("Use the local Cookbook address.", HTTPStatus.FORBIDDEN)
        if origin and urlsplit(origin).netloc != host:
            raise RequestError("This request must come from the Cookbook workspace.", HTTPStatus.FORBIDDEN)

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        name = "index.html" if path == "/" else path.lstrip("/")
        if name.startswith("assets/"):
            name = name.removeprefix("assets/")
        try:
            asset = (ASSETS / name).resolve()
            if not asset.is_relative_to(ASSETS.resolve()) or not asset.is_file():
                raise FileNotFoundError
            body = asset.read_bytes()
        except (OSError, ValueError):
            self._json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        content_type = STATIC_TYPES.get(asset.suffix.lower()) or mimetypes.guess_type(asset.name)[0]
        content_type = content_type or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        self._send(body, content_type)

    def do_POST(self) -> None:
        try:
            self._check_origin()
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            if url.path in ("/api/extract", "/api/preview"):
                filename = query.get("filename", [""])[0]
                document = self._body(MAX_UPLOAD_BYTES)
                mime_type = _media_type(filename, document)
                if url.path == "/api/preview":
                    try:
                        page = int(query.get("page", ["1"])[0])
                    except ValueError:
                        raise RequestError("Choose an existing page.") from None
                    self._json(_preview(document, mime_type, page))
                    return
                try:
                    gemini_key, openai_key = load_api_keys()
                except (OSError, ValueError) as error:
                    raise RequestError(str(error), HTTPStatus.SERVICE_UNAVAILABLE) from None
                self._json(run_pipeline(document, filename, GeminiClient(gemini_key), OpenAIClient(openai_key)))
                return
            if url.path == "/api/review":
                data, report = self._recipe_request()
                review = review_recipe(report["recipe"], data.get("decisions"))
                response = {"review": review}
                if "vegan_plan" in report:
                    response["vegan_plan"] = self._refresh_plan(report, review["recipe"])
                self._json(response)
                return
            if url.path == "/api/adapt":
                self._json(self._adapt())
                return
            raise RequestError("Not found.", HTTPStatus.NOT_FOUND)
        except RequestError as error:
            self._json({"error": str(error)}, error.status)
        except UnreadableRecipeError as error:
            self._json({"error": str(error), "code": "unreadable_blur"}, HTTPStatus.UNPROCESSABLE_ENTITY)
        except ProviderError as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
        except ValueError as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def _recipe_request(self) -> tuple[dict, dict]:
        if self.headers.get_content_type() != "application/json":
            raise RequestError("Send the recipe report as JSON.", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        try:
            data = json.loads(self._body(MAX_REPORT_BYTES))
        except (UnicodeError, json.JSONDecodeError):
            raise RequestError("The request must contain valid JSON.") from None
        if not isinstance(data, dict):
            raise RequestError("The request must contain a recipe report.")
        report = data.get("report")
        if not isinstance(report, dict) or not isinstance(report.get("recipe"), dict):
            raise RequestError("The request must contain a recipe report.")
        return data, report

    def _refresh_plan(self, report: dict, recipe: dict) -> dict:
        plan = report["vegan_plan"]
        if vegan_plan_matches(recipe, plan):
            return plan
        if report.get("example") is True:
            example = json.loads((ASSETS / "example.json").read_text(encoding="utf-8"))
            if vegan_plan_matches(recipe, example.get("vegan_plan")):
                return example["vegan_plan"]
            return {"status": "unavailable", "reason": "The offline example plan no longer matches the reviewed recipe."}
        try:
            key = load_openai_key()
        except (OSError, ValueError):
            return {"status": "unavailable", "reason": "Set OPENAI_API_KEY to refresh the vegan plan."}
        return plan_vegan_recipe(OpenAIClient(key), recipe)

    def _adapt(self) -> dict:
        data, report = self._recipe_request()
        vegan = data.get("vegan", False)
        if type(vegan) is not bool:
            raise RequestError("Vegan preference must be true or false.")
        recipe = report["recipe"]
        if "review" in report:
            submitted_review = report["review"]
            if not isinstance(submitted_review, dict):
                raise RequestError("The review must contain the decisions made on the original recipe.")
            review = review_recipe(recipe, submitted_review.get("decisions"))
            recipe = review["recipe"]
            report = {**report, "review": review}
        try:
            result = adapt_recipe(
                recipe, servings=data.get("servings"),
                original_servings=data.get("original_servings"), vegan=vegan,
                vegan_plan=report.get("vegan_plan"),
            )
        except (AttributeError, KeyError, TypeError):
            raise RequestError("The extraction report contains an invalid recipe object.") from None
        return {**report, "adaptation": result}


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Cookbook recipe workspace.")
    parser.add_argument("--host", default="127.0.0.1", help="Interface to listen on")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), CookbookHandler) as server:
        print(f"Cookbook at http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
