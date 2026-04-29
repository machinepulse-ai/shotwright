"""Shared model display metadata for agent providers."""

from __future__ import annotations

import re
from typing import Any


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _title_segment(segment: str) -> str:
    if not segment:
        return segment
    lowered = segment.lower()
    if lowered in {"gpt", "api", "llm"}:
        return lowered.upper()
    if lowered in {"mini", "nano", "flash", "pro", "max"}:
        return lowered
    return segment[:1].upper() + segment[1:]


def format_model_display_name(model_id: str, name: str | None = None) -> str:
    source = _first_non_empty(name, model_id)
    if not source:
        return "Unknown"

    normalized = source.strip()
    if re.match(r"^gpt[-_]", normalized, flags=re.IGNORECASE):
        return re.sub(r"^gpt[-_]", "GPT-", normalized, flags=re.IGNORECASE).replace("-mini", " mini")

    return " ".join(
        _title_segment(segment)
        for segment in re.split(r"[-_\s]+", normalized)
        if segment
    )


def infer_model_brand(model_id: str, name: str | None = None, model_provider: str | None = None) -> str:
    text = " ".join(
        value.lower()
        for value in (model_id, name or "", model_provider or "")
        if isinstance(value, str)
    )
    if "claude" in text or "anthropic" in text:
        return "Claude"
    if "gemini" in text or "google" in text:
        return "Gemini"
    if "qwen" in text or "dashscope" in text or "alibaba" in text:
        return "Qwen"
    if "gpt" in text or "openai" in text or "o3" in text or "o4" in text:
        return "GPT"
    return "Model"


def infer_model_family(model_id: str, name: str | None = None, model_provider: str | None = None) -> str:
    brand = infer_model_brand(model_id, name, model_provider)
    text = " ".join(value.lower() for value in (model_id, name or "") if isinstance(value, str))
    if brand == "Claude":
        if "sonnet" in text:
            return "Sonnet"
        if "haiku" in text:
            return "Haiku"
        if "opus" in text:
            return "Opus"
    if brand == "Gemini":
        if "flash" in text:
            return "Flash"
        if "pro" in text:
            return "Pro"
    if brand == "Qwen":
        return "Qwen"
    return brand


def infer_model_submodel(model_id: str, name: str | None = None) -> str:
    display_name = format_model_display_name(model_id, name)
    brand = infer_model_brand(model_id, name)
    if brand != "Model" and display_name.lower().startswith(brand.lower()):
        return display_name[len(brand) :].strip(" -")
    return display_name


def build_agent_model_metadata(
    model_id: str,
    *,
    name: str | None = None,
    provider: str | None = None,
    model_provider: str | None = None,
) -> dict[str, Any]:
    display_name = format_model_display_name(model_id, name)
    return {
        "provider": provider,
        "model_provider": model_provider,
        "brand": infer_model_brand(model_id, name, model_provider),
        "family": infer_model_family(model_id, name, model_provider),
        "submodel": infer_model_submodel(model_id, name),
        "display_name": display_name,
    }
