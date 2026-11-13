"""HTTP adapters for the two recipe readers and the OpenAI comparison call."""

import base64
import json
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


def _encode_document(document: bytes, filename: str) -> tuple[str, str]:
    mime_type = MIME_TYPES.get(Path(filename).suffix.lower())
    if not mime_type:
        raise ValueError("Supported input files are PDF, PNG, and JPEG.")
    if not document:
        raise ValueError("The input document is empty.")
    return mime_type, base64.b64encode(document).decode("ascii")


class GeminiClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def transcribe(self, document: bytes, filename: str, prompt: str) -> str:
        mime_type, encoded = _encode_document(document, filename)
        result = _post_json(
            "Gemini",
            GEMINI_URL,
            "x-goog-api-key",
            self._api_key,
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {"text": prompt},
                            {"inlineData": {"mimeType": mime_type, "data": encoded}},
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

    def transcribe(self, document: bytes, filename: str, prompt: str) -> str:
        mime_type, encoded = _encode_document(document, filename)
        data_uri = f"data:{mime_type};base64,{encoded}"
        if mime_type == "application/pdf":
            attachment = {
                "type": "input_file",
                "filename": Path(filename).name,
                "file_data": data_uri,
            }
        else:
            attachment = {"type": "input_image", "image_url": data_uri}

        return self.respond(
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}, attachment],
                }
            ]
        )
