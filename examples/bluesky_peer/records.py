"""app.bsky.* records as USER-AUTHORED raw dicts.

LOUD COMMENT, on purpose: FastATP ships the Lexicon *type system*, never
application schemas (PHILOSOPHY.md; the `app.bsky.*` rule). These builders
live in the example because they are the *user's* records — the same way a
workshop's work-order schema lived in examples/workorder.py. If you want
typed models, define your own `Lexicon` subclasses in YOUR code; the shapes
below follow the published `app.bsky` lexicons (vendored under `reference/`
as language examples only).

Everything here is a plain dict the data model already validates: no floats,
blob objects are the `$type: blob` compound produced by `Repo.add_blob()`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def profile(
    display_name: str,
    description: str | None = None,
    avatar_blob: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """app.bsky.actor.profile — rkey is the literal ``self``."""
    record: dict[str, Any] = {
        "$type": "app.bsky.actor.profile",
        "displayName": display_name,
    }
    if description is not None:
        record["description"] = description
    if avatar_blob is not None:
        record["avatar"] = avatar_blob  # the $type: blob object from add_blob()
    return record


def post(
    text: str,
    *,
    created_at: str | None = None,
    langs: list[str] | None = None,
    image_blob: dict[str, Any] | None = None,
    image_alt: str = "",
) -> dict[str, Any]:
    """app.bsky.feed.post — rkey is a TID. ``image_blob`` embeds one image."""
    record: dict[str, Any] = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": created_at or now_iso(),
    }
    if langs is not None:
        record["langs"] = langs
    if image_blob is not None:
        record["embed"] = {
            "$type": "app.bsky.embed.images",
            "images": [{"image": image_blob, "alt": image_alt}],
        }
    return record


def follow(subject_did: str, *, created_at: str | None = None) -> dict[str, Any]:
    """app.bsky.graph.follow — rkey is a TID."""
    return {
        "$type": "app.bsky.graph.follow",
        "subject": subject_did,
        "createdAt": created_at or now_iso(),
    }
