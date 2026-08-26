"""Makera post-processor.

The Makera Z1 controller is a GRBL-family firmware; this post inherits the
GRBL dialect and adds a program-name header.  The AeroDust air output is
deliberately NOT mapped to an M-code (no proven public reference): by default
a comment is emitted and ``coolant_mcodes["aerodust"]`` is ``None`` in
``PostSettings`` — users can set it to a vendor-confirmed M-code.
"""

from __future__ import annotations

from antcam_rc2.core.post.base import ModalEngine
from antcam_rc2.core.post.grbl import GrblPost
from antcam_rc2.core.post.models import PostContext


class MakeraPost(GrblPost):
    """Post-processor for Makera machines (GRBL-family with Makera naming)."""

    post_id = "makera"
    version = "1.0"

    def header_lines(self, ctx: PostContext, engine: ModalEngine) -> list[str]:
        if not ctx.settings.emit_comments:
            return []
        identity = ctx.settings.program_id or "program"
        return [self.comment(f"Makera {ctx.machine.model} — {identity}")]


__all__ = ["MakeraPost"]
