"""Explainable, local visual checks for uploaded brand assets."""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter

from PIL import Image, ImageOps

WCAG_ALT = "WCAG 2.2 SC 1.1.1 Non-text Content — https://www.w3.org/WAI/WCAG22/Understanding/non-text-content"
WCAG_CONTRAST = "WCAG 2.2 SC 1.4.3 Contrast (Minimum) — https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum"


def _luminance(gray: float) -> float:
    value = gray / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _contrast(first: float, second: float) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _logo_similarity(first: bytes, second: bytes) -> float:
    def digest(data: bytes) -> int:
        with Image.open(io.BytesIO(data)) as original:
            image = ImageOps.exif_transpose(original).convert("RGBA")
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            if bbox:
                image = image.crop(bbox)
            canvas = Image.new("RGBA", (9, 8), "white")
            image.thumbnail((9, 8))
            canvas.alpha_composite(image, ((9 - image.width) // 2, (8 - image.height) // 2))
            pixels = list(canvas.convert("L").get_flattened_data())
            value = 0
            for y in range(8):
                for x in range(8):
                    value = (value << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
            return value
    distance = (digest(first) ^ digest(second)).bit_count()
    return 1.0 - distance / 64


def _palette_colors(sources) -> list[tuple[int, int, int, object]]:
    found = []
    for source in sources:
        if source.status != "active" or source.kind != "rule":
            continue
        for value in re.findall(r"#[0-9a-fA-F]{6}\b", source.title + " " + source.body + " " + source.passage):
            rgb = tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
            found.append((*rgb, source))
    return found


def analyse_visual(data: bytes, role: str, alt_text: str, sources, reference_logo: bytes | None = None) -> tuple[list[dict], str, tuple[int, int]]:
    Image.MAX_IMAGE_PIXELS = 40_000_000
    findings: list[dict] = []
    note = ""
    with Image.open(io.BytesIO(data)) as original:
        original.load()
        if original.width * original.height > 40_000_000:
            raise ValueError("Image dimensions exceed the processing limit")
        image = ImageOps.exif_transpose(original).convert("RGB")
        width, height = image.size
        dimensions = image.size
        preview = image.copy()
        preview.thumbnail((256, 256))

    if role == "informative" and not alt_text.strip():
        findings.append({"kind": "accessibility", "excerpt": "Image alternative text is missing", "explanation": "Informative non-text content needs a text alternative that serves its equivalent purpose.", "severity": "high", "confidence": 1.0, "citation": WCAG_ALT, "suggestion": "Add concise alt text describing the information this image conveys."})

    if role == "logo" and reference_logo:
        similarity = _logo_similarity(data, reference_logo)
        if similarity < 0.78:
            findings.append({"kind": "logo", "excerpt": f"Logo comparison similarity: {similarity:.0%}", "explanation": "The uploaded logo differs from the reference image supplied for this check. Confirm that the asset is an approved logo variant.", "severity": "medium", "confidence": 0.78, "citation": "User-supplied reference logo attached to this visual check; image similarity is a perceptual hash estimate.", "suggestion": "Use the approved reference logo or confirm this variant with your brand administrator."})

    palette = _palette_colors(sources) if role in {"logo", "brand_graphic"} else []
    if palette:
        quantized = preview.quantize(colors=16)
        colors = quantized.getpalette() or []
        counts = quantized.getcolors(256) or []
        total = max(1, sum(count for count, _ in counts))
        outside = 0
        for count, palette_index in counts:
            rgb = tuple(colors[palette_index * 3:palette_index * 3 + 3])
            distance = min(sum((rgb[i] - brand[i]) ** 2 for i in range(3)) ** 0.5 for *brand, _source in palette)
            if distance > 65:
                outside += count
        share = outside / total
        if share > 0.70:
            source = palette[0][3]
            findings.append({"kind": "colour", "excerpt": f"{share:.0%} of sampled image pixels are outside the approved palette tolerance", "explanation": f"The sampled image colours were compared with approved palette values from {source.title}; more than 70% were outside the 65 RGB-distance tolerance. This is a screening signal and is sensitive to photographic content.", "severity": "medium", "confidence": 0.72, "citation": f"RULE #{source.id} — {source.source}, {source.title}, v{source.version}: {source.passage or source.body}", "suggestion": "Confirm the asset role and adjust the graphic colours to the approved palette if this is a brand graphic."})

    if role in {"informative", "brand_graphic"}:
        try:
            import pytesseract
            from pytesseract import Output
            with Image.open(io.BytesIO(data)) as source_image:
                source_image = ImageOps.exif_transpose(source_image).convert("RGB")
                ocr = pytesseract.image_to_data(source_image, output_type=Output.DICT, timeout=20)
            gray = image.convert("L")
            estimates = []
            for index, text in enumerate(ocr.get("text", [])):
                if not text.strip():
                    continue
                try:
                    x, y, w, h = (int(ocr[key][index]) for key in ("left", "top", "width", "height"))
                    confidence = float(ocr["conf"][index])
                except (ValueError, TypeError, KeyError, IndexError):
                    continue
                if confidence < 40 or w < 3 or h < 3 or x < 0 or y < 0 or x + w > width or y + h > height:
                    continue
                crop = gray.crop((x, y, x + w, y + h))
                values = list(crop.get_flattened_data())
                if len(values) > 250_000:
                    continue
                edge = [crop.getpixel((cx, cy)) for cx in range(w) for cy in (0, h - 1)] + [crop.getpixel((cx, cy)) for cy in range(h) for cx in (0, w - 1)]
                bg = statistics.median(edge)
                foreground = [value for value in values if abs(value - bg) > 45]
                if len(foreground) < max(8, len(values) * 0.015):
                    continue
                fg = statistics.median(foreground)
                ratio = _contrast(bg, fg)
                estimates.append((text.strip(), ratio, x, y, w, h))
            low = [item for item in estimates if item[1] < 4.5]
            if low:
                label, ratio, x, y, w, h = min(low, key=lambda item: item[1])
                findings.append({"kind": "contrast", "excerpt": f"Image region x={x} y={y} width={w} height={h}", "explanation": f"OCR detected ‘{label}’. Local grayscale sampling estimated text/background contrast at {ratio:.2f}:1, below 4.5:1. Image-based estimates can be affected by antialiasing, gradients, and text-box detection; verify the pair in the source design.", "severity": "medium", "confidence": 0.66, "citation": WCAG_CONTRAST, "suggestion": "Increase text/background contrast and verify the final colour pair against WCAG 2.2 SC 1.4.3."})
        except Exception:
            # A missing OCR engine must not be reported as a passed contrast check.
            note = "Text contrast could not be measured because OCR box detection was unavailable."

    return findings, note, dimensions
