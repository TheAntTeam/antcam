"""Schema-driven parameter binding: registry schema <-> widget values.

The pure part (``fields_from_schema`` / ``common_parameter_fields``) maps a
Pydantic JSON schema to field specifications; the Qt part builds widgets from
those specs and reads/writes :class:`OperationParameters` without any
per-operation hard-coded UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from antcam_rc2.core.feeds_speeds.models import FeedSpeedOverrides
from antcam_rc2.core.project.models import OperationParameters

FieldKind = Literal["number", "integer", "boolean", "choice", "text"]


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One form field derived from a JSON schema property."""

    key: str
    label: str
    kind: FieldKind
    default: float | int | bool | str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[str, ...] = ()

    def coerce(self, value) -> float | int | bool | str | None:
        if value is None:
            return self.default
        if self.kind == "boolean":
            return bool(value)
        if self.kind == "integer":
            return int(value)
        if self.kind == "number":
            return float(value)
        return str(value)


def fields_from_schema(schema: dict, *, prefix: str = "") -> list[FieldSpec]:
    """Convert a Pydantic JSON schema into :class:`FieldSpec` entries.

    Supports ``type: number/integer/boolean/string`` with ``enum`` (choice),
    ``minimum``/``maximum``/``default`` and ``allOf`` (``const``) defaults.
    """
    fields: list[FieldSpec] = []
    properties = schema.get("properties", {})
    for key, spec in properties.items():
        kind_schema = spec
        if "allOf" in spec:
            merged: dict = {"type": "string"}
            for candidate in spec["allOf"]:
                merged.update({k: v for k, v in candidate.items() if k != "title"})
            kind_schema = merged
        field_type = kind_schema.get("type")
        if field_type == "number":
            kind: FieldKind = "number"
        elif field_type == "integer":
            kind = "integer"
        elif field_type == "boolean":
            kind = "boolean"
        else:
            kind = "choice" if "enum" in kind_schema else "text"
        choices = tuple(str(choice) for choice in kind_schema.get("enum", ()))
        default = kind_schema.get("default")
        fields.append(
            FieldSpec(
                key=key,
                label=spec.get("title", key.replace("_", " ").title()),
                kind=kind,
                default=default if default is not None else (None if kind == "text" else None),
                minimum=kind_schema.get("minimum"),
                maximum=kind_schema.get("maximum"),
                choices=choices,
            )
        )
    return fields


def common_parameter_fields() -> list[FieldSpec]:
    """The fixed common parameters shared by every operation."""
    return [
        FieldSpec("depth_mm", "Depth (mm)", "number", None, minimum=0.0, step=0.1),
        FieldSpec("stepdown_mm", "Stepdown (mm)", "number", None, minimum=0.0, step=0.1),
        FieldSpec("stepover_mm", "Stepover (mm)", "number", None, minimum=0.0, step=0.1),
        FieldSpec("stock_allowance_mm", "Stock allowance (mm)", "number", 0.0, minimum=0.0, step=0.05),
        FieldSpec("finishing_passes", "Finishing passes", "integer", 0, minimum=0),
        FieldSpec("tolerance_mm", "Tolerance (mm)", "number", 0.1, minimum=0.001, step=0.01),
        FieldSpec("climb_cut", "Climb cut", "boolean", False),
        FieldSpec("optimize_path_order", "Optimize path order", "boolean", True),
    ]


_FEED_OVERRIDE_FIELDS: tuple[tuple[str, str, float], ...] = (
    ("rpm", "RPM", 100.0),
    ("cut_feed_mm_min", "Cut feed (mm/min)", 10.0),
    ("plunge_feed_mm_min", "Plunge feed (mm/min)", 10.0),
    ("stepdown_mm", "Stepdown (mm)", 0.1),
    ("stepover_mm", "Stepover (mm)", 0.1),
)


class ParametersBinder(QWidget):
    """Builds a form from common + strategy schema fields and reads/writes it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._form = QFormLayout(self)
        self._fields: list[tuple[FieldSpec, QWidget]] = []
        self._feed_checks: list[tuple[str, QCheckBox]] = []
        self._feed_spins: list[tuple[str, QDoubleSpinBox]] = []

    # ------------------------------------------------------------------ build
    def rebuild(self, schema: dict | None) -> None:
        """Rebuild the form for the given strategy parameter schema."""
        while self._form.rowCount() > 0:
            self._form.removeRow(0)
        self._fields.clear()
        self._feed_checks.clear()
        self._feed_spins.clear()

        for spec in common_parameter_fields():
            self._fields.append((spec, self._make_widget(spec)))
            self._form.addRow(spec.label, self._fields[-1][1])

        if schema:
            separator = QLabel("Strategy parameters")
            separator.setStyleSheet("color: #9a9ca1; margin-top: 6px;")
            self._form.addRow(separator)
            for spec in fields_from_schema(schema):
                self._fields.append((spec, self._make_widget(spec)))
                self._form.addRow(spec.label, self._fields[-1][1])

        separator = QLabel("Feed/speed overrides (auto when cleared)")
        separator.setStyleSheet("color: #9a9ca1; margin-top: 6px;")
        self._form.addRow(separator)
        for key, label, step in _FEED_OVERRIDE_FIELDS:
            row = QWidget(self)
            layout = QFormLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            check = QCheckBox("auto", row)
            spin = QDoubleSpinBox(row)
            spin.setRange(0.0, 100000.0)
            spin.setDecimals(2)
            spin.setSingleStep(step)
            spin.setEnabled(False)
            check.toggled.connect(spin.setEnabled)
            check.setChecked(True)
            layout.addRow(label, spin)
            layout.addRow(check)
            self._form.addRow(row)
            self._feed_checks.append((key, check))
            self._feed_spins.append((key, spin))

    def _make_widget(self, spec: FieldSpec) -> QWidget:
        if spec.kind == "boolean":
            widget = QCheckBox(self)
            widget.setChecked(bool(spec.default))
            return widget
        if spec.kind == "choice":
            widget = QComboBox(self)
            widget.addItems(list(spec.choices))
            if spec.default is not None and str(spec.default) in spec.choices:
                widget.setCurrentText(str(spec.default))
            return widget
        if spec.kind == "integer":
            widget = QSpinBox(self)
            widget.setRange(int(spec.minimum or 0), 100000)
            widget.setValue(int(spec.default or 0))
            return widget
        if spec.kind == "text":
            from PySide6.QtWidgets import QLineEdit

            widget = QLineEdit(self)
            if spec.default is not None:
                widget.setText(str(spec.default))
            return widget
        widget = QDoubleSpinBox(self)
        widget.setRange(float(spec.minimum or 0.0), 100000.0)
        widget.setDecimals(3)
        widget.setSingleStep(spec.step or 0.1)
        if spec.default is not None:
            widget.setValue(float(spec.default))
        return widget

    # ------------------------------------------------------------------ read/write
    def to_parameters(self) -> OperationParameters:
        """Read the current widget values into an :class:`OperationParameters`."""
        common: dict[str, object] = {}
        strategy: dict = {}
        for spec, widget in self._fields:
            value = self._widget_value(widget)
            if value is None:
                continue
            if spec.kind in {"number", "integer"} and spec.default is None and float(value) == 0.0:
                continue  # untouched optional numeric field -> unset
            if spec.key in {
                "depth_mm",
                "stepdown_mm",
                "stepover_mm",
                "stock_allowance_mm",
                "finishing_passes",
                "tolerance_mm",
                "climb_cut",
                "optimize_path_order",
            }:
                common[spec.key] = value
            else:
                strategy[spec.key] = value
        overrides: dict[str, float] = {}
        for key, check in self._feed_checks:
            if not check.isChecked():
                spin = dict(self._feed_spins)[key]
                overrides[key] = spin.value()
        parameters = common
        parameters["strategy_parameters"] = strategy
        parameters["feed_speed_overrides"] = FeedSpeedOverrides(**overrides)
        return OperationParameters.model_validate(parameters)

    def set_parameters(self, parameters: OperationParameters, schema: dict | None) -> None:
        """Populate the widgets from an :class:`OperationParameters`."""
        if self._fields is None or not self._fields:
            return
        values = parameters.model_dump()
        for spec, widget in self._fields:
            key = spec.key
            value = values.get(key)
            if key in parameters.strategy_parameters:
                value = parameters.strategy_parameters[key]
            self._set_widget_value(widget, value)
        override_values = parameters.feed_speed_overrides.model_dump()
        for key, check in self._feed_checks:
            value = override_values.get(key)
            if value is None:
                check.setChecked(True)
            else:
                check.setChecked(False)
                spin = dict(self._feed_spins)[key]
                spin.setValue(float(value))

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _widget_value(widget: QWidget):
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        if isinstance(widget, QLineEdit):
            return widget.text() or None
        if isinstance(widget, QSpinBox):
            return widget.value()
        if isinstance(widget, QDoubleSpinBox):
            return widget.value()
        return None

    @staticmethod
    def _set_widget_value(widget: QWidget, value) -> None:
        if value is None:
            return
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
        elif isinstance(widget, QComboBox):
            text = str(value)
            if widget.findText(text) >= 0:
                widget.setCurrentText(text)
        elif isinstance(widget, QLineEdit):
            widget.setText(str(value))
        elif isinstance(widget, QSpinBox):
            widget.setValue(int(value))
        elif isinstance(widget, QDoubleSpinBox):
            widget.setValue(float(value))


__all__ = ["FieldSpec", "ParametersBinder", "common_parameter_fields", "fields_from_schema"]
