"""Render PDFs as ordered, lossless page images for the Luna reader."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from math import isfinite
from typing import TypeVar

import pymupdf
from PIL import Image


MAX_RASTER_PAGES = 32
MAX_RASTER_PNG_BYTES = 36_000_000
MIN_LONG_EDGE = 1_600
MAX_LONG_EDGE = 3_000

_PDF_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cookbook-pdf")
_Result = TypeVar("_Result")


def run_pdf_task(task: Callable[..., _Result], *args) -> _Result:
    """Keep PyMuPDF operations from preview and extraction on one thread."""
    return _PDF_WORKER.submit(task, *args).result()


def rasterize_pdf(document: bytes) -> list[bytes] | None:
    """Return lossless PNGs, or None if the PDF exceeds the raster budget."""
    return run_pdf_task(_rasterize_pdf, document)


def _rasterize_pdf(document: bytes) -> list[bytes] | None:
    try:
        with pymupdf.open(stream=document, filetype="pdf") as pdf:
            if not pdf.is_pdf:
                raise ValueError("The input is not a PDF.")
            if pdf.needs_pass:
                raise ValueError("Unlock the PDF before running OCR.")
            if not pdf.page_count:
                raise ValueError("The PDF has no pages.")
            if pdf.page_count > MAX_RASTER_PAGES:
                return None

            pages = []
            png_bytes = 0
            for page in pdf:
                dimensions = (page.rect.width, page.rect.height)
                if not all(isfinite(length) and length > 0 for length in dimensions):
                    raise ValueError(f"PDF page {page.number + 1} has invalid dimensions.")
                long_edge = max(dimensions)
                scale = min(max(1, MIN_LONG_EDGE / long_edge), MAX_LONG_EDGE / long_edge)
                pixels = page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False,
                )
                with Image.frombytes("RGB", (pixels.width, pixels.height), pixels.samples) as image:
                    with BytesIO() as buffer:
                        image.save(buffer, format="PNG", compress_level=3)
                        png_bytes += buffer.tell()
                        if png_bytes > MAX_RASTER_PNG_BYTES:
                            return None
                        pages.append(buffer.getvalue())
            return pages
    except (RuntimeError, OSError) as error:
        raise ValueError("Could not render the PDF pages. Check that the file is readable.") from error
