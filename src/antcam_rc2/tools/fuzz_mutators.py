"""Deterministic lightweight fuzzer for the DXF/SVG importers.

Produces mutated byte payloads from a seed (no external dependencies) and
imports them, asserting that only documented domain errors surface — never
unexpected exceptions.  Lives in ``tools/`` (not in the installed wheel).
"""

from __future__ import annotations

import random
from pathlib import Path

from antcam_rc2.core.errors import AntcamError


def mutate(data: bytes, seed: int, ops: int = 4) -> bytes:
    """Return a deterministic mutation of ``data`` using ``seed``.

    Mutation kinds: truncation at seeded lengths, byte flips at seeded
    offsets, removal of XML tag delimiters/attributes, corruption of the
    ``$INSUNITS`` header and duplication of a slice.
    """
    rng = random.Random(seed)
    payload = bytearray(data)
    for _ in range(ops):
        kind = rng.randrange(5)
        if kind == 0 and payload:
            length = rng.randrange(len(payload))
            payload = payload[:length]
        elif kind == 1 and payload:
            offset = rng.randrange(len(payload))
            payload[offset] = rng.randrange(256)
        elif kind == 2:
            text = bytes(payload)
            for token in (b"<", b">", b'stroke="black"', b"inkscape:label"):
                if token in text:
                    text = text.replace(token, b"", 1)
                    break
            payload = bytearray(text)
        elif kind == 3:
            text = bytes(payload)
            if b"$INSUNITS" in text:
                text = text.replace(b"$INSUNITS", b"$INSUNITZ", 1)
            payload = bytearray(text)
        elif payload:
            start = rng.randrange(len(payload))
            length = min(rng.randrange(1, 32), len(payload) - start)
            payload.extend(payload[start : start + length])
    return bytes(payload)


def fuzz_import(path: Path, seeds: range, *, max_bytes: int | None = None) -> list[str]:
    """Import every mutant and return unexpected-exception messages.

    A mutant is acceptable when it imports (with or without diagnostics) or
    raises an :class:`AntcamError`; anything else is reported.
    """
    from antcam_rc2.core.io import import_file

    source = path.read_bytes()
    if max_bytes is not None:
        source = source[:max_bytes]
    problems: list[str] = []
    for seed in seeds:
        mutant = mutate(source, seed)
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=path.suffix, delete=False) as handle:
            handle.write(mutant)
            temp_path = Path(handle.name)
        try:
            import_file(temp_path)
        except AntcamError:
            pass  # documented domain error
        except Exception as exc:  # noqa: BLE001 - fuzz must surface anything else
            problems.append(f"seed {seed}: {type(exc).__name__}: {exc}")
        finally:
            temp_path.unlink(missing_ok=True)
    return problems
