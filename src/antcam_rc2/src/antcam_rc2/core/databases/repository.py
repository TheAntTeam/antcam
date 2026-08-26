"""Read-only loading and lookup of validated machining catalog bundles."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from antcam_rc2.core.databases.models import (
    CatalogEnvelope,
    CoolingProfile,
    MachineProfile,
    MaterialProfile,
    Tool,
)
from antcam_rc2.core.errors import CatalogError, ConfigurationError

_CatalogModel = type[MachineProfile] | type[Tool] | type[MaterialProfile] | type[CoolingProfile]
_CATALOGS: tuple[tuple[str, _CatalogModel], ...] = (
    ("cooling", CoolingProfile),
    ("machines", MachineProfile),
    ("materials", MaterialProfile),
    ("tools", Tool),
)


@dataclass(frozen=True, slots=True)
class CatalogBundle:
    """An atomically loaded, immutable set of machining catalogs."""

    machines: Mapping[str, MachineProfile]
    tools: Mapping[str, Tool]
    materials: Mapping[str, MaterialProfile]
    cooling: Mapping[str, CoolingProfile]
    versions: Mapping[str, str]


class CatalogRepository:
    """Read-only repository backed by package resources or a directory."""

    def __init__(self, read_text: Callable[[str], str]) -> None:
        self._read_text = read_text
        self._bundle: CatalogBundle | None = None

    @classmethod
    def from_directory(cls, directory: Path) -> CatalogRepository:
        """Create a repository that loads a complete bundle from ``directory``."""

        def read_text(filename: str) -> str:
            path = directory / filename
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise ConfigurationError(f"catalog file is missing: {path}") from exc
            except OSError as exc:
                raise ConfigurationError(f"catalog file cannot be read: {path}") from exc

        return cls(read_text)

    @classmethod
    def from_package(cls) -> CatalogRepository:
        """Create a repository backed by JSON catalogs shipped with RC2."""

        package_files = files("antcam_rc2.data")

        def read_text(filename: str) -> str:
            resource = package_files.joinpath(filename)
            try:
                return resource.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise ConfigurationError(f"packaged catalog file is missing: {filename}") from exc
            except OSError as exc:
                raise ConfigurationError(f"packaged catalog file cannot be read: {filename}") from exc

        return cls(read_text)

    def load(self) -> CatalogBundle:
        """Load and validate the entire bundle once, atomically."""
        if self._bundle is not None:
            return self._bundle

        envelopes = {catalog_id: self._load_catalog(catalog_id, model) for catalog_id, model in _CATALOGS}
        machines = self._index(envelopes["machines"].items)
        tools = self._index(envelopes["tools"].items)
        materials = self._index(envelopes["materials"].items)
        cooling = self._index(envelopes["cooling"].items)
        self._validate_references(machines, cooling)

        bundle = CatalogBundle(
            machines=MappingProxyType(machines),
            tools=MappingProxyType(tools),
            materials=MappingProxyType(materials),
            cooling=MappingProxyType(cooling),
            versions=MappingProxyType(
                {catalog_id: envelopes[catalog_id].schema_version for catalog_id, _ in _CATALOGS}
            ),
        )
        self._bundle = bundle
        return bundle

    def machine(self, catalog_id: str) -> MachineProfile:
        """Return one machine profile or raise a stable catalog error."""
        return self._lookup(self.load().machines, catalog_id, "machine")

    def tool(self, catalog_id: str) -> Tool:
        """Return one tool or raise a stable catalog error."""
        return self._lookup(self.load().tools, catalog_id, "tool")

    def material(self, catalog_id: str) -> MaterialProfile:
        """Return one material profile or raise a stable catalog error."""
        return self._lookup(self.load().materials, catalog_id, "material")

    def cooling(self, catalog_id: str) -> CoolingProfile:
        """Return one cooling profile or raise a stable catalog error."""
        return self._lookup(self.load().cooling, catalog_id, "cooling profile")

    def catalog_versions(self) -> dict[str, str]:
        """Return the schema versions of all loaded catalogs in stable order."""
        return dict(self.load().versions)

    def _load_catalog(self, catalog_id: str, model: _CatalogModel) -> CatalogEnvelope[Any]:
        filename = f"{catalog_id}.json"
        try:
            payload = json.loads(self._read_text(filename))
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"catalog {filename} contains invalid JSON") from exc

        envelope_type = self._envelope_type(model)
        try:
            Draft202012Validator(envelope_type.model_json_schema()).validate(payload)
        except JsonSchemaValidationError as exc:
            raise ConfigurationError(f"catalog {filename} failed schema validation: {exc.message}") from exc
        try:
            envelope = envelope_type.model_validate(payload)
        except ValidationError as exc:
            raise ConfigurationError(f"catalog {filename} failed model validation: {exc}") from exc
        if envelope.catalog_id != catalog_id:
            raise ConfigurationError(
                f"catalog {filename} declares catalog_id {envelope.catalog_id!r}, expected {catalog_id!r}"
            )
        return envelope

    @staticmethod
    def _index(items: tuple[Any, ...]) -> dict[str, Any]:
        return {item.id: item for item in items}

    @staticmethod
    def _envelope_type(model: _CatalogModel) -> type[CatalogEnvelope[Any]]:
        if model is CoolingProfile:
            return CatalogEnvelope[CoolingProfile]
        if model is MachineProfile:
            return CatalogEnvelope[MachineProfile]
        if model is MaterialProfile:
            return CatalogEnvelope[MaterialProfile]
        if model is Tool:
            return CatalogEnvelope[Tool]
        raise AssertionError(f"unsupported catalog model: {model!r}")

    @staticmethod
    def _lookup(records: Mapping[str, Any], catalog_id: str, label: str) -> Any:
        try:
            return records[catalog_id]
        except KeyError as exc:
            raise CatalogError(f"unknown {label}: {catalog_id}") from exc

    @staticmethod
    def _validate_references(machines: Mapping[str, MachineProfile], cooling: Mapping[str, CoolingProfile]) -> None:
        for machine in machines.values():
            unknown_ids = set(machine.supported_cooling_ids) - cooling.keys()
            if unknown_ids:
                unknown = ", ".join(sorted(unknown_ids))
                raise ConfigurationError(f"machine {machine.id} references unknown cooling: {unknown}")
