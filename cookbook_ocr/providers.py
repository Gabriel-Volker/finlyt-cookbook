"""HTTP adapters for the two recipe readers and the OpenAI comparison call."""

import base64
import json
import logging
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .extraction import PAGE_MARKER
from .raster import PreparedPdf, rasterize_pdf


GEMINI_MODEL = "gemini-3.1-flash-lite"
OPENAI_MODEL = "gpt-6-luna"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
OPENAI_URL = "https://api.openai.com/v1/responses"
MIME_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
LOGGER = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A provider did not return a usable response."""


def _post_json(
    provider: str,
    url: str,
    key_header: str,
    key_value: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    request.add_unredirected_header(key_header, key_value)

    try:
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
    except HTTPError as error:
        error.close()
        raise ProviderError(f"{provider} returned HTTP {error.code}.") from None
    except (URLError, OSError, HTTPException):
        raise ProviderError(f"Could not complete the request to {provider}.") from None
    except (UnicodeError, json.JSONDecodeError):
        raise ProviderError(f"{provider} returned invalid JSON.") from None

    if not isinstance(result, dict):
        raise ProviderError(f"{provider} returned an unexpected response.")
    return result


def _document_mime_type(document: bytes, filename: str) -> str:
    mime_type = MIME_TYPES.get(Path(filename).suffix.lower())
    if not mime_type:
        raise ValueError("Supported input files are PDF, PNG, and JPEG.")
    if not document:
        raise ValueError("The input document is empty.")
    return mime_type


def _base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class GeminiClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def transcribe(self, document: bytes, filename: str, prompt: str) -> str:
        mime_type = _document_mime_type(document, filename)
        result = _post_json(
            "Gemini",
            GEMINI_URL,
            "x-goog-api-key",
            self._api_key,
            {
                "systemInstruction": {"parts": [{"text": prompt}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"inlineData": {"mimeType": mime_type, "data": _base64(document)}},
                        ],
                    }
                ],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 4096},
            },
        )
        candidates = result.get("candidates") or []
        if not candidates or candidates[0].get("finishReason") != "STOP":
            raise ProviderError("Gemini did not return a completed transcription.")

        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "\n".join(
            part["text"]
            for part in parts
            if part.get("text") and not part.get("thought")
        )
        if not text.strip():
            raise ProviderError("Gemini returned an empty transcription.")
        return text


class OpenAIClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def respond(self, **payload: Any) -> str:
        result = _post_json(
            "OpenAI",
            OPENAI_URL,
            "Authorization",
            f"Bearer {self._api_key}",
            {"max_output_tokens": 4096, **payload, "model": OPENAI_MODEL, "store": False},
        )
        if result.get("status") != "completed":
            incomplete = result.get("incomplete_details") or {}
            if incomplete.get("reason") == "max_output_tokens":
                raise ProviderError("OpenAI reached the output token limit.")
            raise ProviderError("OpenAI did not return a completed response.")

        text = "\n".join(
            part["text"]
            for item in result.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text" and part.get("text")
        )
        if not text.strip():
            raise ProviderError("OpenAI returned no output text.")
        return text

    def transcribe(
        self, document: bytes, filename: str, prompt: str, *, prepared_pdf: PreparedPdf | None = None,
    ) -> str:
        mime_type = _document_mime_type(document, filename)
        if mime_type != "application/pdf":
            image = {"type": "input_image", "image_url": f"data:{mime_type};base64,{_base64(document)}"}
            return self._transcribe_parts(prompt, [image])

        pages = prepared_pdf.pages if prepared_pdf is not None else rasterize_pdf(document)
        if pages is None:
            LOGGER.warning("The PDF exceeds the local raster budget. Luna will receive the original PDF.")
            attachment = {
                "type": "input_file", "filename": "recipe.pdf",
                "file_data": f"data:application/pdf;base64,{_base64(document)}",
            }
            return self._transcribe_parts(prompt, [attachment])

        attachments = [
            {"type": "input_image", "image_url": f"data:image/png;base64,{_base64(page)}", "detail": "high"}
            for page in pages
        ]
        raster_prompt = (
            f"{prompt.rstrip()}\n\nThe attachment is {len(pages)} page images in physical page order. "
            "Treat each image as a separate page, including images with no readable text. "
            "Use one generated page marker per image. Printed page numbers remain document text."
        )
        text = self._transcribe_parts(raster_prompt, attachments)
        expected = [str(number) for number in range(1, len(pages) + 1)]
        if PAGE_MARKER.findall(text) != expected:
            raise ProviderError("OpenAI did not return the expected page markers for the rasterized PDF.")
        return text

    def _transcribe_parts(self, prompt: str, attachments: list[dict]) -> str:
        return self.respond(
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": attachments},
            ]
        )
