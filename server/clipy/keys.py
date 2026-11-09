"""
One "universal" API key slot. The provider is recognised from the key's prefix, the key
is checked against that provider once, and a sensible model is picked and remembered.

    sk-ant-…  Anthropic (Claude, via the official SDK)
    sk-or-…   OpenRouter
    gsk_…     Groq
    AIza…     Google Gemini
    sk-…      OpenAI or DeepSeek (both use bare "sk-"; tried in the likelier order)

The key lives in data/settings.json on this machine only and is never sent back to the
browser in full.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path

import anthropic

SETTINGS = Path(__file__).resolve().parent.parent / "data" / "settings.json"

CLAUDE_MODEL = "claude-opus-5-5"

PROVIDERS = {
    "anthropic": "Claude",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
}


class KeyProblem(Exception):
    """The key is malformed, rejected, or the provider couldn't be reached."""


class Rejected(KeyProblem):
    """The provider answered and said no (as opposed to being unreachable)."""


def detect(key: str) -> list[str]:
    """Providers this key could belong to, most likely first."""
    if key.startswith("sk-ant-"):
        return ["anthropic"]
    if key.startswith("sk-or-"):
        return ["openrouter"]
    if key.startswith("gsk_"):
        return ["groq"]
    if key.startswith("AIza"):
        return ["gemini"]
    if key.startswith(("sk-proj-", "sk-svcacct-", "sk-admin-")):
        return ["openai"]
    if key.startswith("sk-"):
        # DeepSeek keys are "sk-" plus 32 hex digits; older OpenAI keys are also bare "sk-".
        return ["deepseek", "openai"] if re.fullmatch(r"sk-[0-9a-f]{32}", key) else ["openai", "deepseek"]
    raise KeyProblem("unrecognised key — use a Claude, OpenAI, DeepSeek, Gemini, OpenRouter or Groq key")


# ---------- storage ----------

def load() -> dict | None:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return data if data.get("key") else None


def public(data: dict | None) -> dict:
    if not data:
        return {"set": False}
    key = data["key"]
    return {
        "set": True,
        "provider": data["provider"],
        "label": PROVIDERS[data["provider"]],
        "model": data["model"],
        "masked": f"{key[:6]}…{key[-4:]}",
    }


def save(key: str) -> dict:
    key = key.strip()
    candidates = detect(key)
    for i, provider in enumerate(candidates):
        try:
            model = VALIDATORS[provider](key)
            break
        except Rejected:
            # Not this provider's key; try the next one that shares the prefix.
            if i == len(candidates) - 1:
                raise
    data = {"key": key, "provider": provider, "model": model}
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def clear() -> None:
    SETTINGS.unlink(missing_ok=True)


# ---------- HTTP helper for the REST providers ----------

def _http(url: str, headers: dict | None = None, body: dict | None = None, timeout: float = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "User-Agent": "clipy-id", **(headers or {})},
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code in (401, 403):
            raise Rejected("the provider rejected this key") from e
        raise KeyProblem(f"provider error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise KeyProblem(f"can't reach the provider ({e.reason})") from e


def _pick(ids: list[str], preferences: list[str]) -> str | None:
    """First id matching the earliest preference pattern."""
    for pattern in preferences:
        for i in ids:
            if re.search(pattern, i):
                return i
    return None


# ---------- validation: check the key, choose a model ----------

def _validate_anthropic(key: str) -> str:
    try:
        anthropic.Anthropic(api_key=key, max_retries=1).models.retrieve(CLAUDE_MODEL)
    except anthropic.AuthenticationError as e:
        raise Rejected("the provider rejected this key") from e
    except anthropic.PermissionDeniedError as e:
        raise KeyProblem("this key has no access to Claude models") from e
    except anthropic.APIConnectionError as e:
        raise KeyProblem("can't reach the Claude API") from e
    return CLAUDE_MODEL


def _validate_openai(key: str) -> str:
    ids = [m["id"] for m in _http("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"})["data"]]
    model = _pick(ids, [r"^gpt-5(\.\d+)?$", r"^gpt-5", r"^gpt-4\.1$", r"^gpt-4o$", r"^gpt-"])
    if not model:
        raise KeyProblem("no chat model available for this key")
    return model


def _validate_openrouter(key: str) -> str:
    _http("https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {key}"})
    ids = [m["id"] for m in _http("https://openrouter.ai/api/v1/models")["data"]]
    return _pick(ids, [r"^anthropic/claude-opus-5", r"^anthropic/claude-sonnet-5", r"^anthropic/claude", r"^openai/gpt-5"]) or ids[0]


def _validate_groq(key: str) -> str:
    ids = [m["id"] for m in _http("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})["data"]]
    model = _pick(ids, [r"gpt-oss-120b", r"llama-3\.3-70b", r"70b", r"llama"])
    if not model:
        raise KeyProblem("no chat model available for this key")
    return model


def _validate_gemini(key: str) -> str:
    models = _http(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200")["models"]
    ids = [m["name"].removeprefix("models/") for m in models if "generateContent" in m.get("supportedGenerationMethods", [])]
    model = _pick(ids, [r"^gemini-[\d.]+-pro$", r"^gemini-[\d.]+-flash$", r"pro", r"flash"])
    if not model:
        raise KeyProblem("no Gemini model available for this key")
    return model


def _validate_deepseek(key: str) -> str:
    ids = [m["id"] for m in _http("https://api.deepseek.com/models", {"Authorization": f"Bearer {key}"})["data"]]
    # The chat model supports JSON output mode; the reasoner may not.
    model = _pick(ids, [r"^deepseek-chat$", r"chat", r"^deepseek"])
    if not model:
        raise KeyProblem("no DeepSeek chat model available for this key")
    return model


VALIDATORS = {
    "anthropic": _validate_anthropic,
    "openai": _validate_openai,
    "openrouter": _validate_openrouter,
    "groq": _validate_groq,
    "gemini": _validate_gemini,
    "deepseek": _validate_deepseek,
}

BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com",
}


# ---------- asking for JSON ----------

def ask_json(system: str, user: str, schema: dict) -> dict:
    """Send one prompt to whichever provider the stored key belongs to; return parsed JSON."""
    data = load()
    if not data:
        raise KeyProblem("no API key set")
    provider, key, model = data["provider"], data["key"], data["model"]

    if provider == "anthropic":
        client = anthropic.Anthropic(api_key=key)
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            # A policy decline is re-run on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if response.stop_reason == "refusal":
            raise KeyProblem("Claude declined to pick clips for this video")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)

    instructions = f"{system}\n\nReply with JSON only, matching this schema:\n{json.dumps(schema)}"
    if provider == "gemini":
        res = _http(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            body={
                "systemInstruction": {"parts": [{"text": instructions}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=300,
        )
        text = "".join(p.get("text", "") for p in res["candidates"][0]["content"]["parts"])
    else:
        res = _http(
            f"{BASE_URLS[provider]}/chat/completions",
            {"Authorization": f"Bearer {key}"},
            {
                "model": model,
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": user}],
                "response_format": {"type": "json_object"},
            },
            timeout=300,
        )
        text = res["choices"][0]["message"]["content"]
    # Some models still wrap JSON in a code fence.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)
