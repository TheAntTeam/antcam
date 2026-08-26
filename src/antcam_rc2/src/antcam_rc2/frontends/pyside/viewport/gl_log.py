"""Optional OpenGL diagnostic logging for the viewport.

Enable with the ``ANTCAM_GL_DEBUG`` environment variable:

    set ANTCAM_GL_DEBUG=1            (cmd)
    $env:ANTCAM_GL_DEBUG="1"         (PowerShell)
    ANTCAM_GL_DEBUG=1 antcam-rc2 gui-demo

Lines are written to stderr prefixed with ``[GL]`` so they can be captured
without interfering with the UI:  ``... 2> gl_debug.log``.
"""

from __future__ import annotations

import os
import sys

_DEBUG = os.environ.get("ANTCAM_GL_DEBUG", "") not in ("", "0")


def gl_log(*args) -> None:  # noqa: ANN002 - variadic diagnostic helper
    """Emit one ``[GL]`` line to stderr when ``ANTCAM_GL_DEBUG`` is set."""
    if _DEBUG:
        print("[GL]", *args, file=sys.stderr, flush=True)


__all__ = ["gl_log"]
