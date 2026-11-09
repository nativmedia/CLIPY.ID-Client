"""
Ready-to-post titles and descriptions for each finished clip, per platform, written by the
LLM behind the stored key from the clip's own transcript. Saved to social.json in the
project folder so edits made in the editor stick.
"""

from __future__ import annotations

import json

from . import keys

# Field limits as the platforms enforce them (characters).
PLATFORMS = {
    "youtube": {"label": "YouTube", "fields": {"title": 100, "description": 5000}},
    "tiktok": {"label": "TikTok", "fields": {"description": 2200}},
    "instagram": {"label": "Instagram", "fields": {"description": 2200}},
    "facebook": {"label": "Facebook", "fields": {"description": 2200}},
    "x": {"label": "X", "fields": {"description": 280}},
}

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "youtube_title": {"type": "string"},
                    "youtube_description": {"type": "string"},
                    "tiktok": {"type": "string"},
                    "instagram": {"type": "string"},
                    "facebook": {"type": "string"},
                    "x": {"type": "string"},
                },
                "required": ["n", "youtube_title", "youtube_description", "tiktok", "instagram", "facebook", "x"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}

SYSTEM = (
    "You write the post text for short vertical video clips cut from a longer video, one set per "
    "platform, in the same language as the clip. Match each platform's style: YouTube Shorts gets a "
    "searchable title (max 100 characters) and a 2-4 sentence description ending with a few hashtags "
    "including #Shorts; TikTok and Instagram Reels get a punchy caption with a hook line, a line of "
    "context and 3-6 relevant hashtags; Facebook Reels gets a friendly 1-3 sentence caption with 1-3 "
    "hashtags; X gets one sharp post under 260 characters including at most 2 hashtags. Describe only "
    "what the clip actually contains; no clickbait it doesn't deliver, no invented facts."
)


def path(project_dir):
    return project_dir / "social.json"


def load(project_dir) -> dict | None:
    try:
        return json.loads(path(project_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save(project_dir, data: dict) -> dict:
    path(project_dir).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def generate(project_dir, video_title: str, clips: list[dict], words: list[dict]) -> dict:
    """One LLM call for every finished clip; returns {"<n>": {platform: {field: text}}}."""
    parts = []
    for c in clips:
        text = " ".join(w["text"] for w in words if w["start"] >= c["start"] - 0.05 and w["end"] <= c["end"] + 0.3)
        parts.append(f"Clip {c['n']} — working title: {c['title']}\nTranscript: {text}")
    user = f"Source video: {video_title}\n\n" + "\n\n".join(parts)
    out = keys.ask_json(SYSTEM, user, SCHEMA)

    data: dict[str, dict] = {}
    for c in out.get("clips", []):
        data[str(c["n"])] = {
            "youtube": {"title": c["youtube_title"][:100], "description": c["youtube_description"][:5000]},
            "tiktok": {"description": c["tiktok"][:2200]},
            "instagram": {"description": c["instagram"][:2200]},
            "facebook": {"description": c["facebook"][:2200]},
            "x": {"description": c["x"][:280]},
        }
    return save(project_dir, data)
