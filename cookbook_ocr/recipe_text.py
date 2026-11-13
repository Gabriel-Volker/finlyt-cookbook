"""Decode text input and assign page markers under application control."""

from .extraction import PAGE_MARKER


def decode_recipe_text(document: bytes) -> str:
    try:
        text = document.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("Save the recipe as a UTF-8 text file.") from None
    if any(ord(char) < 32 and char not in "\r\n\t" for char in text):
        raise ValueError("The file content does not match plain text.")
    if not text.strip():
        raise ValueError("The recipe file is empty.")
    return text


def page_transcription(pages: list[str]) -> str:
    return "\n".join(
        f"[page: {index}]\n{PAGE_MARKER.sub(lambda marker: '> ' + marker[0], page).rstrip()}"
        for index, page in enumerate(pages, 1)
    )
