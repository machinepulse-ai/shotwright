"""GitHub token resolution without exposing secret values in logs."""

from __future__ import annotations

import os

from app.config import settings
from app.database import get_admin_collection


def apply_github_token_environment(token: str | None) -> str | None:
    normalized = token.strip() if isinstance(token, str) else ""
    if not normalized:
        return None
    os.environ["GITHUB_TOKEN"] = normalized
    os.environ["SHOTWRIGHT_GITHUB_TOKEN"] = normalized
    return normalized


async def resolve_github_token() -> str | None:
    configured_token = apply_github_token_environment(settings.github_token)
    if configured_token:
        return configured_token

    doc = await get_admin_collection().find_one({"_id": "settings"})
    token = doc.get("github_token") if doc else None
    return apply_github_token_environment(token)
