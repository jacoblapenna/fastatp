"""The poster: reads the curator's signed selections, posts as the persona.

The poster holds the persona pen IN-PROCESS (writes are direct method calls
on the hosted repo — the pen never crosses a wire in any form) and a
``subscribe`` capability in the curator's repo (how the doorman admits its
firehose connection). Every inbound record is verified — commit signature
against the curator's resolved document, MST inversion, the works — by
``RepoMirror`` before the poster acts on it.
"""

from __future__ import annotations

import base64
from typing import Any

from fastatp.repo.sync import RepoMirror
from fastatp.server.peer_server import PeerServer

from .. import records  # the user-authored app.bsky.* raw-dict builders
from .curator import SELECTION_NSID


class Poster:
    """Turns verified curator selections into persona posts with image blobs."""

    def __init__(self, server: PeerServer, persona_did: str, resolver: Any) -> None:
        self.server = server  # writes commit via the primary (persona) repo
        self.persona_did = persona_did
        self.mirror: RepoMirror | None = None
        self.resolver = resolver
        self.posted: list[str] = []  # rkeys of posts made, in order

    def ensure_profile(self, display_name: str, description: str) -> None:
        if self.server.repo.get_record("app.bsky.actor.profile", "self") is None:
            self.server.put_record(
                "app.bsky.actor.profile", "self", records.profile(display_name, description)
            )

    def attach(self, curator_did: str) -> RepoMirror:
        """A verifying mirror of the curator's gated repo."""
        self.mirror = RepoMirror(curator_did, resolver=self.resolver)
        return self.mirror

    async def handle_frame(self, frame: bytes) -> list[str]:
        """Verify one curator frame; post for each new selection record."""
        assert self.mirror is not None, "attach() before handling frames"
        new_posts: list[str] = []
        for record in await self.mirror.handle_frame(frame):
            if record["action"] == "delete" or not record["path"].startswith(SELECTION_NSID):
                continue
            selection = record["record"]
            # data-model $bytes is UNPADDED base64; restore exactly the
            # padding the length requires (blindly appending "==" breaks
            # for payloads whose encoded length is already a multiple of 4)
            b64 = selection["imagePng"]["$bytes"]
            png = base64.b64decode(b64 + "=" * (-len(b64) % 4))
            # the image rides as a real blob on the persona repo
            blob = self.server.repo.add_blob(png, "image/png")
            post = records.post(
                selection["caption"],
                image_blob=blob.model_dump(by_alias=True),
                image_alt=selection.get("alt", ""),
            )
            event = self.server.put_record("app.bsky.feed.post", f"post-{len(self.posted)}", post)
            assert event.commit.did == self.persona_did  # signed by the persona pen
            self.posted.append(f"post-{len(self.posted)}")
            new_posts.append(self.posted[-1])
        return new_posts
