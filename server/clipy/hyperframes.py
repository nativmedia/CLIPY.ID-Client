"""HyperFrames renderer for the isolated caption and thumbnail experiment."""

from __future__ import annotations

import html
import json
import math
import shutil
import subprocess
from collections import deque
from pathlib import Path

from PIL import Image, ImageDraw

from . import captions

ROOT = Path(__file__).resolve().parents[2] / "hyperframes-renderer"
CLI = ROOT / "node_modules/hyperframes/bin/hyperframes.mjs"


def command(*args, cwd: Path, on_progress=None):
    if not CLI.exists():
        raise RuntimeError("Install the HyperFrames renderer dependencies with npm ci in hyperframes-renderer")
    process = subprocess.Popen(["node", str(CLI), *map(str, args)], cwd=cwd,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace")
    tail = deque(maxlen=30)
    try:
        for line in process.stdout:
            tail.append(line)
            if on_progress and line.startswith("@hf-progress "):
                report = json.loads(line[len("@hf-progress "):])
                if "pct" in report:
                    on_progress(min(1, max(0, report["pct"] / 100)))
        code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
    if code:
        raise RuntimeError(f"HyperFrames failed ({code}): {''.join(tail)[-1800:]}")
    return "".join(tail)


def document(folder: Path, width: int, height: int, duration: float, body: str, css: str,
             config: dict, font: str = "montserrat", font_weight: str = "regular") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    assets = folder / "assets"
    assets.mkdir(exist_ok=True)
    weight = captions.font_weight_value(font, font_weight)
    if font == "poppins":
        faces = {
            100: "Poppins-Thin.ttf", 400: "Poppins-Regular.ttf",
            700: "Poppins-Bold.ttf", 800: "Poppins-ExtraBold.ttf",
        }
        for value, name in faces.items():
            shutil.copyfile(captions.FONT_DIR / name, assets / name)
        font_face = "".join(
            f'@font-face{{font-family:Clipy;src:url(assets/{name});font-weight:{value};font-display:swap}}'
            for value, name in faces.items()
        )
    else:
        shutil.copyfile(captions.FONT_DIR / captions.font_file(font, font_weight), assets / "font.ttf")
        span = "100 900" if font == "montserrat" else "100 800" if font == "jetbrains-mono" else str(weight)
        font_face = f"@font-face{{font-family:Clipy;src:url(assets/font.ttf);font-weight:{span};font-display:swap}}"
    gsap = ROOT / "node_modules/gsap/dist/gsap.min.js"
    if gsap.is_file():
        shutil.copyfile(gsap, assets / "gsap.min.js")
    effects = (ROOT / "effects.js").read_text(encoding="utf-8")
    config = {**config, "duration": duration}
    encoded = json.dumps(config, ensure_ascii=False).replace("<", "\\u003c")
    path = folder / "index.html"
    path.write_text(f"""<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width={width}, height={height}">
<script src="assets/gsap.min.js"></script>
<style>
{font_face}
* {{box-sizing:border-box}}
html,body {{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:transparent}}
#root {{width:100%;height:100%;font-family:Clipy,sans-serif;color:#fff}}
.cue {{visibility:hidden;position:absolute;inset:0}}
.word {{position:absolute;display:flex;align-items:center;justify-content:center;overflow:visible}}
.glyph {{white-space:pre;position:relative;z-index:1;paint-order:stroke fill;transform-origin:center}}
.accent {{position:absolute;opacity:0;pointer-events:none}}
{css}
</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{duration}"
data-width="{width}" data-height="{height}" data-fps="30">{body}</div>
<script type="application/json" id="overlay-config">{encoded}</script>
<script>{effects}</script></body></html>""", encoding="utf-8")
    return path


def caption_composition(folder, words, style, width, height, duration, *, stacked=False):
    font = style.font if style.font in captions.FONTS else "montserrat"
    color = captions.COLORS.get(style.color, "#FFFFFF")
    center = (height * (0.17 + (style.position - 1) * .11) if style.position is not None else
              height * .88 if stacked else captions.layout_for(width, height).baseline)
    animation = "pop" if style.animation == "word" else style.animation
    font_weight = captions.font_weight_value(font, style.font_weight)
    body, cues = [], []
    outline = 0
    for ci, cue in enumerate(captions._caption_cues(words, style, width, height)):
        if cue.start >= duration:
            continue
        size = cue.size
        outline = 0
        if animation == "glow":
            outline = max(outline, round(size * .035), 1)
        shadow = ""
        block = cue.line_height * len(cue.rows)
        y = max(block / 2, min(height - block / 2, center)) - block / 2
        spoken = [row[0] for line in cue.rows for row in line]
        next_time = {id(w): n.start for w, n in zip(spoken, spoken[1:])}
        data = {"id": f"cue-{ci}", "start": max(0, cue.start), "end": min(duration, cue.end), "words": []}
        markup = []
        for ri, row in enumerate(cue.rows):
            x = (width - sum(item[2] for item in row) - (len(row) - 1) * cue.space) / 2
            for wi, (word, text, advance) in enumerate(row):
                ident = f"word-{ci}-{ri}-{wi}"
                start = max(cue.start, word.start, 0)
                end = min(cue.end, next_time.get(id(word), cue.end), duration)
                if end <= start:
                    x += advance + cue.space
                    continue
                data["words"].append({"id": ident, "start": start, "end": end, "outline": outline})
                accent = f"inset:12% -{size * .08}px;background:{color};border-radius:{size * .08}px;font-weight:{font_weight};"
                content = ""
                if animation == "underline":
                    accent = f"left:0;right:0;bottom:6%;height:{max(2,size*.06)}px;background:{color};transform-origin:left;"
                elif animation in ("glow", "karaoke"):
                    content = html.escape(text)
                    accent = f"inset:0;display:flex;align-items:center;justify-content:center;color:{color};white-space:pre;font-weight:{font_weight};"
                    if animation == "glow":
                        accent += f"color:transparent;text-shadow:0 0 {size*.12}px {color},0 0 {size*.2}px {color};"
                markup.append(f'<div class="word" id="{ident}" style="left:{x:.3f}px;top:{y:.3f}px;width:{advance:.3f}px;'
                              f'height:{cue.line_height}px;font-size:{size}px;line-height:1">'
                              f'<span class="accent" aria-hidden="true" data-layout-ignore style="{accent}">{content}</span>'
                              f'<span class="glyph" style="font-weight:{font_weight};-webkit-text-stroke:{outline}px #000;{shadow}">{html.escape(text)}</span></div>')
                x += advance + cue.space
            y += cue.line_height
        body.append(f'<div class="cue" id="{data["id"]}">{"".join(markup)}</div>')
        cues.append(data)
    css = ".glyph {opacity:0}" if animation == "reveal" else ""
    return document(folder, width, height, duration, "".join(body), css,
                    {"cues": cues, "color": color, "animation": animation, "outline": outline}, font, style.font_weight)


def render_captions(words, style, out: Path, width, height, duration, *, stacked=False, on_progress=None):
    folder = out.parent / f"{out.stem}_hyperframes"
    duration = math.ceil(duration * 30) / 30
    caption_composition(folder, words, style, width, height, duration, stacked=stacked)
    command("render", folder, "--format", "webm", "--workers", "1", "--fps", "30",
            "--output", out.resolve(), "--quality", "standard", cwd=ROOT,
            on_progress=(lambda value: on_progress(value * .65)) if on_progress else None)
    return out


def render_title(image: Path, out: Path, text: str, style: str, position: str | None = None):
    """Render the thumbnail title through HyperFrames, then composite the still on its image."""
    from . import thumbs
    with Image.open(image) as source:
        width, height = source.size
        base = source.convert("RGBA")
    font, lines = thumbs._fit_text(ImageDraw.Draw(base),
                                  text, int(width * .82), int(height * .35),
                                  int(min(width, height) * (.11 if height >= width else .09)))
    pos = position or "bc"
    vertical = {"t": "flex-start", "m": "center", "b": "flex-end"}[pos[0]]
    horizontal = {"l": "flex-start", "c": "center", "r": "flex-end"}[pos[1]]
    size = font.size
    stroke = max(2, size * .08)
    paint = f"-webkit-text-stroke:{stroke}px #000;paint-order:stroke fill;"
    if style == "pop":
        paint += f"color:#FFE600;text-shadow:{size*.06}px {size*.08}px {size*.1}px #000;"
    elif style == "shadow":
        paint = f"text-shadow:0 {size*.08}px {size*.12}px #000;"
    elif style in ("box", "pill", "stamp"):
        paint = {"box": "background:#000;color:#fff;",
                 "pill": "background:#fff;color:#12141a;border-radius:999px;",
                 "stamp": "background:#9e1a2b;color:#fff;border:2px solid #ffe0e5;"}[style] + "padding:.1em .25em;"
    elif style == "underline":
        paint += "text-decoration:underline;text-decoration-color:#62ebf1;text-underline-offset:.12em;"
    gradient = "background:linear-gradient(transparent 45%,#000d);" if style == "bar" else ""
    body = '<div class="title-wrap">' + "".join(f'<span class="title-line">{html.escape(line)}</span>' for line in lines) + "</div>"
    css = f""".title-wrap {{width:100%;height:100%;padding:{height * .08}px {width * .07}px;display:flex;flex-direction:column;
justify-content:{vertical};align-items:{horizontal};gap:.06em;{gradient}font-size:{size}px}}
.title-line {{max-width:100%;line-height:1.25;white-space:pre;{paint}}}"""
    folder = out.parent / f"{out.stem}_hyperframes"
    document(folder, width, height, 1, body, css, {}, "montserrat")
    # Keep the exact production title face in the browser.
    shutil.copyfile(thumbs.FONT, folder / "assets/font.ttf")
    command("snapshot", folder, "--at", "0.5", "--no-end", "--output", folder / "still", cwd=ROOT)
    frames = list((folder / "still").glob("*.png"))
    if len(frames) != 1:
        raise RuntimeError("HyperFrames thumbnail snapshot is missing")
    with Image.open(frames[0]) as overlay:
        Image.alpha_composite(base, overlay.convert("RGBA")).convert("RGB").save(out, quality=92)
    return out

