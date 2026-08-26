"""File-based fixture library for saving and loading fixture definitions."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from antcam_rc2.core.errors import ProjectError
from antcam_rc2.core.project.models import Fixture


class FixtureLibraryIndex(BaseModel):
    """Index of fixtures in the library."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixtures: tuple[Fixture, ...] = ()


class FixtureLibrary:
    """File-based fixture library: list/save/load/delete fixtures.

    The library stores fixtures in a JSON index file and mesh files in a
    ``meshes/`` subdirectory. Each fixture's ``mesh_path`` is stored as a
    relative path (e.g., ``meshes/fix_abc12345.step``) for portability.
    """

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.meshes_dir = base_dir / "meshes"
        self.index_path = base_dir / "fixtures.json"
        self.meshes_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _load_index(self) -> FixtureLibraryIndex:
        """Load and validate the fixture index."""
        if not self.index_path.exists():
            return FixtureLibraryIndex()
        try:
            payload = self.index_path.read_text(encoding="utf-8")
            return FixtureLibraryIndex.model_validate_json(payload)
        except (json.JSONDecodeError, OSError) as exc:
            raise ProjectError(f"failed to load fixture library index: {exc}") from exc

    def _save_index(self, index: FixtureLibraryIndex) -> None:
        """Atomically write the fixture index."""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.base_dir,
                prefix=f".{self.index_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                json_str = json.dumps(index.model_dump(mode="json"), ensure_ascii=True, indent=2, sort_keys=True)
                handle.write(json_str + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.index_path)
        except OSError as exc:
            raise ProjectError(f"failed to write fixture library index: {exc}") from exc
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)

    def list_fixtures(self) -> list[Fixture]:
        """Return all fixtures in the library, sorted by name."""
        index = self._load_index()
        return sorted(index.fixtures, key=lambda f: f.name.lower())

    def get_fixture(self, fixture_id: str) -> Fixture | None:
        """Return a fixture by ID, or None if not found."""
        index = self._load_index()
        for fixture in index.fixtures:
            if fixture.id == fixture_id:
                return fixture
        return None

    def save_fixture(self, fixture: Fixture, mesh_source: Path | None = None) -> Fixture:
        """Save a fixture to the library, copying mesh if provided.

        If ``mesh_source`` is given, it is copied to ``meshes/<fixture_id>.<ext>``
        and the fixture's ``mesh_path`` is updated to the relative path.
        Returns the saved fixture (with updated mesh_path if applicable).
        """
        index = self._load_index()
        fixtures = list(index.fixtures)

        # Remove existing fixture with same ID
        fixtures = [f for f in fixtures if f.id != fixture.id]

        # Handle mesh copy
        updated_fixture = fixture
        if mesh_source is not None and mesh_source.exists():
            ext = mesh_source.suffix.lower()
            if ext not in (".step", ".stp", ".stl"):
                raise ProjectError(f"unsupported mesh format: {ext} (supported: .step, .stp, .stl)")
            mesh_dest = self.meshes_dir / f"{fixture.id}{ext}"
            shutil.copy2(mesh_source, mesh_dest)
            # Store relative path from base_dir
            rel_path = mesh_dest.relative_to(self.base_dir)
            updated_fixture = fixture.model_copy(update={"mesh_path": str(rel_path)})

        fixtures.append(updated_fixture)
        self._save_index(FixtureLibraryIndex(fixtures=tuple(fixtures)))
        return updated_fixture

    def delete_fixture(self, fixture_id: str) -> None:
        """Delete a fixture from the library and its associated mesh file."""
        index = self._load_index()
        fixtures = list(index.fixtures)
        fixture_to_delete = next((f for f in fixtures if f.id == fixture_id), None)
        if fixture_to_delete is None:
            return

        # Delete mesh file if exists
        if fixture_to_delete.mesh_path:
            mesh_path = self.base_dir / fixture_to_delete.mesh_path
            if mesh_path.exists():
                mesh_path.unlink(missing_ok=True)

        fixtures = [f for f in fixtures if f.id != fixture_id]
        self._save_index(FixtureLibraryIndex(fixtures=tuple(fixtures)))

    @staticmethod
    def get_default() -> FixtureLibrary:
        """Return the default user fixture library (~/.antcam/fixtures)."""
        from pathlib import Path

        return FixtureLibrary(Path.home() / ".antcam" / "fixtures")
