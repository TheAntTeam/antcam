"""GRBL post-processor.

GRBL-family output: ``;`` comments, ``M6 T#`` tool change, ``G4 P`` dwell
(units from ``PostSettings.dwell_units`` — seconds on GRBL 1.1+, milliseconds
on 0.9-class firmware), ``M2`` program end, ``M8``/``M7`` coolant.
"""

from __future__ import annotations

from antcam_rc2.core.post.base import BasePostProcessor


class GrblPost(BasePostProcessor):
    """Post-processor for GRBL controllers."""

    post_id = "grbl"
    version = "1.0"

    def comment(self, text: str) -> str:
        return f"; {text}"

    def tool_change_line(self, tool_number: int) -> str:
        return f"M6 T{tool_number}"

    def end_line(self) -> str:
        return "M2"


__all__ = ["GrblPost"]
