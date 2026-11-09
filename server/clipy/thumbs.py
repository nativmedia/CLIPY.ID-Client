"""
Thumbnails: a frame from the clip (or the user's own image) at the short's size, with the
clip title set on it in one of a few styles.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT = Path(__file__).resolve().parent.parent / "fonts" / "Montserrat-Black.ttf"

TITLE_STYLES = {
    "none": "No title",
    "bold": "Bold white",
    "pop": "Yellow pop",
    "box": "Black box",
    "bar": "Gradient bar",
    "shadow": "Soft shadow",
    "pill": "White pill",
    "underline": "Cyan underline",
    "stamp": "Red stamp",
}


BRAND_STYLES = {
    "plain": "Plain",
    "outline": "Outline",
    "shadow": "Soft shadow",
    "box": "Black box",
    "pill": "White pill",
}
BRAND_OPACITIES = [20, 40, 60, 80, 100]
BRAND_FONT = Path(__file__).resolve().parent.parent / "fonts" / "Montserrat-ExtraBold.ttf"


def _brand_text(text: str, style: str, size: int) -> Image.Image:
    """The branding text as a transparent image, drawn in one of BRAND_STYLES."""
    font = ImageFont.truetype(str(BRAND_FONT), size)
    left, top, right, bottom = font.getbbox(text)
    tw, th = right - left, bottom - top
    stroke = max(2, size // 9) if style == "outline" else 0
    pad_x, pad_y = (round(size * 0.55), round(size * 0.35)) if style in ("box", "pill") else (stroke + size // 4, stroke + size // 4)
    img = Image.new("RGBA", (tw + pad_x * 2, th + pad_y * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    at = (pad_x - left, pad_y - top)
    if style == "box":
        draw.rectangle((0, 0, img.width - 1, img.height - 1), fill=(0, 0, 0, 215))
        draw.text(at, text, font=font, fill=(255, 255, 255, 255))
    elif style == "pill":
        draw.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=img.height // 2, fill=(255, 255, 255, 240))
        draw.text(at, text, font=font, fill=(0, 0, 0, 255))
    elif style == "shadow":
        shade = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(shade).text((at[0] + size // 14, at[1] + size // 10), text, font=font, fill=(0, 0, 0, 200))
        img = Image.alpha_composite(img, shade.filter(ImageFilter.GaussianBlur(size // 8)))
        ImageDraw.Draw(img).text(at, text, font=font, fill=(255, 255, 255, 255))
    else:
        draw.text(at, text, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    return img


def build_brand(out: Path, width: int, height: int, logo: Path | None, text: str, style: str, opacity: int) -> Path | None:
    """
    The branding mark at its final size in output pixels: the logo, the text, or both side by
    side, faded to the chosen opacity. Video and thumbnail both stamp this same image.
    """
    text = text.strip()
    if not logo and not text:
        return None
    short = min(width, height)
    parts: list[Image.Image] = []
    label = _brand_text(text, style, round(short * 0.042)) if text else None
    if logo:
        mark = ImageOps.exif_transpose(Image.open(logo)).convert("RGBA")
        if label:
            # Beside text, the logo matches the text block's height.
            h = round(label.height * 1.25)
            mark = mark.resize((max(1, round(mark.width * h / mark.height)), h), Image.LANCZOS)
        else:
            w = round(short * 0.16)
            mark = mark.resize((w, max(1, round(mark.height * w / mark.width))), Image.LANCZOS)
        parts.append(mark)
    if label:
        parts.append(label)
    gap = round(short * 0.012) if len(parts) > 1 else 0
    img = Image.new("RGBA", (sum(p.width for p in parts) + gap * (len(parts) - 1), max(p.height for p in parts)), (0, 0, 0, 0))
    x = 0
    for p in parts:
        img.alpha_composite(p, (x, (img.height - p.height) // 2))
        x += p.width + gap
    if opacity < 100:
        alpha = img.getchannel("A").point(lambda a: round(a * opacity / 100))
        img.putalpha(alpha)
    img.save(out)
    return out


def add_brand(image: Path, brand: Path, position: str, margin: int) -> Path:
    """Stamp the branding mark onto the thumbnail at the same spot as in the video."""
    img = Image.open(image).convert("RGB")
    mark = Image.open(brand).convert("RGBA")
    W, H = img.size
    x = {"l": margin, "c": (W - mark.width) // 2, "r": W - mark.width - margin}[position[1]]
    y = {"t": margin, "m": (H - mark.height) // 2, "b": H - mark.height - margin}[position[0]]
    img.paste(mark, (x, y), mark)
    img.save(image, quality=92)
    return image


def prepare(source: Path, out: Path, width: int, height: int) -> Path:
    """Cover-fit any image to the output size."""
    img = ImageOps.exif_transpose(Image.open(source)).convert("RGB")
    ImageOps.fit(img, (width, height), Image.LANCZOS).save(out, quality=92)
    return out


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for word in text.split():
        if draw.textlength(word, font=font) > max_width:
            chunk = ""
            for char in word:
                if chunk and draw.textlength(chunk + char, font=font) > max_width:
                    lines.append(chunk)
                    chunk = ""
                chunk += char
            lines.append(chunk)
            continue
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and draw.textlength(trial, font=font) <= max_width:
            lines[-1] = trial
        else:
            lines.append(word)
    return lines


def _fit_text(draw, text: str, max_width: int, max_height: int, start_size: int):
    size = max(1, start_size)
    while True:
        font = ImageFont.truetype(str(FONT), size)
        lines = _wrap(draw, text, font, max_width)
        widest = max(draw.textlength(line, font=font) for line in lines)
        line_h = int(sum(font.getmetrics()) * 1.05)
        if (len(lines) <= 4 and widest <= max_width and line_h * len(lines) <= max_height) or size == 1:
            return font, lines
        size = max(1, int(size * 0.92))


def _draw_title(img: Image.Image, title: str, style: str, center_y: float, size: float, bar_at: str, *, preserve_case=False, position=None) -> Image.Image:
    """
    Draw the title onto an RGBA image in one of TITLE_STYLES. `center_y` places the text block
    (fraction of height); the "bar" style instead sits on a dark gradient at `bar_at`
    ("top" or "bottom").
    """
    W, H = img.size
    draw = ImageDraw.Draw(img)
    text = title.strip() if preserve_case else title.strip().upper()
    font, lines = _fit_text(draw, text, int(W * 0.86), int(H * 0.38), int(min(W, H) * size))
    ascent, descent = font.getmetrics()
    line_h = int((ascent + descent) * 1.05)
    block_h = line_h * len(lines)

    margin_x = int(W * 0.07)
    margin_y = int(H * 0.08)

    def line_x(line):
        width = draw.textlength(line, font=font)
        return {"l": margin_x, "r": W - margin_x - width, "c": (W - width) / 2}[position[1] if position else "c"]

    y = int(H * center_y) - block_h // 2
    if position:
        y = {"t": margin_y, "m": (H - block_h) // 2, "b": H - margin_y - block_h}[position[0]]

    if style == "bar":
        if position:
            bar_at = "top" if position[0] == "t" else "bottom"
        # Dark gradient from the chosen edge, text left-aligned on it.
        grad = Image.new("L", (1, H))
        for row in range(H):
            depth = (row - H * 0.45) / (H * 0.55) if bar_at == "bottom" else (H * 0.4 - row) / (H * 0.4)
            grad.putpixel((0, row), int(235 * max(0.0, depth) ** 1.2))
        shade = Image.new("RGBA", (W, H), (0, 0, 0, 255))
        shade.putalpha(grad.resize((W, H)))
        img = Image.alpha_composite(img, shade)
        draw = ImageDraw.Draw(img)
        if not position:
            y = H - block_h - margin_y if bar_at == "bottom" else margin_y
        for line in lines:
            draw.text((line_x(line) if position else margin_x, y), line, font=font, fill=(255, 255, 255, 255),
                      stroke_width=max(2, font.size // 14) if position else 0, stroke_fill=(0, 0, 0, 255))
            y += line_h
        return img

    margin = int(H * 0.06)
    y = max(margin, min(H - margin - block_h, y))
    stroke = max(4, font.size // 9)
    for line in lines:
        w = draw.textlength(line, font=font)
        x = line_x(line)
        if style == "box":
            pad = font.size * 0.18
            draw.rectangle((x - pad, y - pad * 0.4, x + w + pad, y + line_h - pad * 0.2), fill=(0, 0, 0, 255))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        elif style in ("pop", "shadow"):
            shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow).text((x + stroke, y + stroke * 1.5), line, font=font,
                                        fill=(0, 0, 0, 170 if style == "pop" else 240))
            img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(stroke)))
            draw = ImageDraw.Draw(img)
            fill = (255, 230, 0, 255) if style == "pop" else (255, 255, 255, 255)
            outline = stroke if style == "pop" else max(1, font.size // 34)
            draw.text((x, y), line, font=font, fill=fill, stroke_width=outline, stroke_fill=(0, 0, 0, 255))
        elif style in ("pill", "stamp"):
            left, top, right, bottom = draw.textbbox((x, y), line, font=font)
            pad_x, pad_y = font.size * 0.28, font.size * 0.14
            bounds = (left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)
            if style == "pill":
                draw.rounded_rectangle(bounds, radius=(bottom - top) / 2 + pad_y, fill=(255, 255, 255, 255))
                draw.text((x, y), line, font=font, fill=(18, 20, 26, 255))
            else:
                draw.rectangle(bounds, fill=(158, 26, 43, 255), outline=(255, 224, 229, 255),
                               width=max(1, font.size // 20))
                draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        else:  # bold and underline share the same outlined text
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            if style == "underline":
                _, _, _, bottom = draw.textbbox((x, y), line, font=font, stroke_width=stroke)
                rule_y = bottom + max(1, font.size // 30)
                draw.rectangle((x, rule_y, x + w, rule_y + max(1, font.size // 17)), fill=(98, 235, 241, 255))
        y += line_h
    return img


def thumbnail_text(config: dict, clip: dict, context: str = "") -> str:
    """Use the clip title for auto mode, preserving manual and hidden options."""
    if config.get("text_mode") == "none":
        return ""
    if config.get("text_mode") != "auto":
        return (config.get("text") or "").strip()
    if config.get("title_style") == "none":
        return ""
    # Automatic thumbnails use the exact clip title. Manual text and the
    # option to hide text remain controlled by the user's existing settings.
    return (clip.get("title") or "").strip()


def add_title(image: Path, out: Path, title: str, style: str, *, custom_text: str = "", number: int | None = None, sequence: str = "number", position: str | None = None) -> Path:
    img = Image.open(image).convert("RGBA")
    custom_text = custom_text.strip()
    if custom_text:
        title = custom_text
        style = "bold" if style == "none" else style
    if number is not None and sequence != "off":
        prefix = {"letter": chr(64 + number), "episode": f"Ep. {number}",
                  "part": f"Part {number}", "clip": f"Klip {number}"}.get(sequence, str(number))
        title = f"{prefix} · {title}" if style != "none" else prefix
        style = "bold" if style == "none" else style
    if style != "none" and title.strip():
        W, H = img.size
        img = _draw_title(img, title, style, 0.62 if H > W else 0.58, 0.11 if H >= W else 0.09, "bottom", preserve_case=bool(custom_text), position=position)
    img.convert("RGB").save(out, quality=92)
    return out


def title_overlay(out: Path, width: int, height: int, title: str, style: str) -> Path:
    """
    The title on a transparent frame, for the first seconds of a clip without a dubbed intro.
    It sits in the upper part of the picture, clear of the captions lower down; "No title"
    falls back to bold white, since the point is to show it.
    """
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    img = _draw_title(img, title, "bold" if style == "none" else style, 0.2 if height > width else 0.22,
                      0.085 if height >= width else 0.07, "top")
    img.save(out)
    return out
