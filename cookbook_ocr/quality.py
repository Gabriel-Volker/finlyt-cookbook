"""Measure strong blur signals without deciding whether a recipe is readable."""

from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat


ANALYSIS_EDGE = 1440
MAX_IMAGE_PIXELS = 16_000_000
MIN_IMAGE_EDGE = 320
MIN_LONG_EDGE = 640
TILE_EDGE = 128
MIN_TEXTURED_TILES = 3
MIN_TILE_CONTRAST = 3.0
MAX_PEAK_LAPLACIAN = 9
MAX_PEAK_DETAIL_RATIO = 0.12

_LAPLACIAN = ImageFilter.Kernel(
    (3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128,
)
_WIDER_BLUR = ImageFilter.GaussianBlur(3)


@dataclass(frozen=True)
class BlurAssessment:
    page: int
    suspected: bool
    textured_tiles: int = 0
    peak_laplacian: int | None = None
    peak_detail_ratio: float | None = None


def _peak_laplacian(image: Image.Image) -> int:
    # Pillow leaves the outer pixel unchanged when applying a custom kernel.
    with image.filter(_LAPLACIAN) as response:
        with response.crop((1, 1, response.width - 1, response.height - 1)) as interior:
            low, high = interior.getextrema()
    return max(128 - low, high - 128)


def _tile_detail(image: Image.Image) -> list[float]:
    scores = []
    with image.filter(_WIDER_BLUR) as softened, ImageChops.difference(image, softened) as detail:
        # Ignore the one-pixel perimeter and account for contrast locally.
        for y in range(1, image.height - 1, TILE_EDGE):
            for x in range(1, image.width - 1, TILE_EDGE):
                right = min(x + TILE_EDGE, image.width - 1)
                bottom = min(y + TILE_EDGE, image.height - 1)
                if min(right - x, bottom - y) < TILE_EDGE // 3:
                    continue
                box = (x, y, right, bottom)
                with image.crop(box) as tile:
                    contrast = ImageStat.Stat(tile).stddev[0]
                if contrast >= MIN_TILE_CONTRAST:
                    with detail.crop(box) as tile:
                        scores.append(ImageStat.Stat(tile).rms[0] / contrast)
    return scores


def _on_white(image: Image.Image) -> Image.Image:
    if "A" not in image.getbands() and "transparency" not in image.info:
        return image.convert("RGB")
    color = Image.new("RGB", image.size, "white")
    with image.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
        color.paste(rgba, mask=alpha)
    return color


def _assess_pixels(page: int, color: Image.Image) -> BlurAssessment:
    with color.convert("L") as gray:
        fine = _peak_laplacian(gray)
        if max(gray.size) > ANALYSIS_EDGE:
            gray.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.LANCZOS)
            fine = max(fine, _peak_laplacian(gray))
        scores = _tile_detail(gray)
    wider = max(scores, default=None)
    suspected = (
        len(scores) >= MIN_TEXTURED_TILES and fine <= MAX_PEAK_LAPLACIAN
        and wider is not None and wider <= MAX_PEAK_DETAIL_RATIO
    )
    if suspected:
        # Color may carry fine writing that disappeared in luminance.
        for channel in "RGB":
            with color.getchannel(channel) as band:
                fine = max(fine, _peak_laplacian(band))
                if fine > MAX_PEAK_LAPLACIAN:
                    suspected = False
                    break
                band.thumbnail((ANALYSIS_EDGE, ANALYSIS_EDGE), Image.Resampling.LANCZOS)
                wider = max(wider, max(_tile_detail(band), default=0.0))
                if wider > MAX_PEAK_DETAIL_RATIO:
                    suspected = False
                    break
    return BlurAssessment(page, suspected, len(scores), fine, wider)


def _assess_page(page: int, data: bytes) -> BlurAssessment:
    try:
        with Image.open(BytesIO(data)) as source:
            if (
                source.width * source.height > MAX_IMAGE_PIXELS
                or min(source.size) < MIN_IMAGE_EDGE or max(source.size) < MIN_LONG_EDGE
                or getattr(source, "is_animated", False)
            ):
                return BlurAssessment(page, False)
            with ImageOps.exif_transpose(source) as oriented, _on_white(oriented) as color:
                return _assess_pixels(page, color)
    except (Image.DecompressionBombError, OSError, ValueError):
        # An inconclusive or unsupported image cannot support a blur rejection.
        return BlurAssessment(page, False)


def assess_blur(pages: Iterable[bytes]) -> list[BlurAssessment]:
    """Return the local signal for each image in physical page order."""
    return [_assess_page(page, data) for page, data in enumerate(pages, 1)]
