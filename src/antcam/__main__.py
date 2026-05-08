"""Module entry point for ``python -m antcam``.

Delegates to the Typer application defined in :mod:`antcam.cli`.
"""

from .cli import app

if __name__ == "__main__":
    app()
