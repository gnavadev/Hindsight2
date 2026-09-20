"""
Prepare screenshots for vision APIs.

Full-monitor PNGs are huge. Downscaling and JPEG encoding cuts payload size
and vision-tile tokens while keeping on-screen text readable.
"""

import logging
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 1920px is enough to read code / quiz text; 4K screenshots otherwise explode
# image tokens. JPEG 85 stays well under Z.AI's 5MB-per-image limit.
MAX_SIDE = 1920
JPEG_QUALITY = 85


def prepare_image_for_llm(
    image: Image.Image,
    max_side: int = MAX_SIDE,
    quality: int = JPEG_QUALITY,
) -> Image.Image:
    """
    Return an RGB image resized to fit inside max_side x max_side.
    Callers should encode as JPEG.
    """
    img = image
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    w, h = img.size
    longest = max(w, h)
    if longest > max_side:
        scale = max_side / float(longest)
        new_size: Tuple[int, int] = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        logger.debug(f"Resized image {w}x{h} -> {img.size[0]}x{img.size[1]}")

    return img


def annotate_page(image: Image.Image, page: int, total: int) -> Image.Image:
    """Draw a 'Page N of M' bar so the model can order multi-page shots."""
    if total <= 1:
        return image
    img = image.convert("RGB") if image.mode != "RGB" else image.copy()
    draw = ImageDraw.Draw(img)
    label = f"Page {page} of {total}"
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
    except Exception:
        try:
            font = ImageFont.truetype("arial.ttf", 28)
        except Exception:
            font = ImageFont.load_default()
    pad_x, pad_y = 10, 6
    try:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = 140, 20
    bar_h = th + pad_y * 2
    draw.rectangle((0, 0, tw + pad_x * 2, bar_h), fill=(0, 0, 0))
    draw.text((pad_x, pad_y), label, fill=(255, 255, 255), font=font)
    return img


def annotate_pages(images: List[Image.Image]) -> List[Image.Image]:
    total = len(images)
    if total <= 1:
        return list(images)
    return [annotate_page(img, i + 1, total) for i, img in enumerate(images)]
