"""LinuxCNC post-processor.

LinuxCNC (EMC2) output: ``( ... )`` comments, ``T# M6`` tool change, ``G4 P``
dwell in seconds, ``M2`` program end, ``M8``/``M7`` coolant, explicit
``G17 G21 G90`` setup.
"""

from __future__ import annotations

from antcam_rc2.core.post.base import BasePostProcessor


class LinuxCncPost(BasePostProcessor):
    """Post-processor for LinuxCNC / EMC2 controllers."""

    post_id = "linuxcnc"
    version = "1.0"

    def comment(self, text: str) -> str:
        return f"( {text} )"

    def tool_change_line(self, tool_number: int) -> str:
        return f"T{tool_number} M6"

    def end_line(self) -> str:
        return "M2"


__all__ = ["LinuxCncPost"]
