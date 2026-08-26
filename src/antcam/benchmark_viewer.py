from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QVBoxLayout, QWidget

from antcam.viewer import AntCamViewerWindow


@dataclass(frozen=True)
class RoughingOperationSummary:
    op_id: str
    strategy: str
    feature_type: str
    style: str
    pass_count: int
    path_count: int
    tool_id: str
    tool_diameter: float
    cut_feed: Optional[float]
    plunge_feed: Optional[float]
    motion_count: int
    cut_segment_count: int
    cut_length: float
    estimated_cut_minutes: Optional[float]
    machining_class: str
    feature_subtype: str
    geometry_source: str


class RoughingBenchmarkWindow(AntCamViewerWindow):
    """Dedicated debug window showing only roughing paths plus textual analysis."""

    def __init__(
        self,
        model,
        features,
        working_plane_normal=(0.0, 0.0, 1.0),
        contour_shadow=None,
        perimeter=None,
        vertical_arc_groups=None,
        toolpath_plan=None,
        diagnostics: Optional[dict[str, Any]] = None,
        planner_config: Optional[Any] = None,
    ):
        self._benchmark_diagnostics = diagnostics or {}
        self._benchmark_planner_config = planner_config
        self._selected_operation_id: Optional[str] = None
        self._roughing_summary_by_id: dict[str, RoughingOperationSummary] = {}
        self._roughing_operations_by_id: dict[str, Any] = {}
        super().__init__(
            model,
            features,
            working_plane_normal=working_plane_normal,
            contour_shadow=contour_shadow,
            perimeter=perimeter,
            vertical_arc_groups=vertical_arc_groups,
            toolpath_plan=toolpath_plan,
        )
        self.setWindowTitle("AntCAM Roughing Benchmark")
        self.resize(1500, 920)
        self._toolpath_mode_filter = "roughing"
        self._toolpath_legend_label.setText(self._benchmark_toolpath_legend_html())
        self._install_benchmark_layout()
        self._refresh_benchmark_sidebar()
        self._update_benchmark_status()

    def _install_benchmark_layout(self) -> None:
        view_widget = self.takeCentralWidget()

        splitter = QSplitter(Qt.Horizontal, self)
        if view_widget is not None:
            splitter.addWidget(view_widget)

        panel = QWidget(splitter)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(8, 8, 8, 8)
        panel_layout.setSpacing(8)

        diagnostics_label = QLabel("Diagnostics")
        self._diagnostics_view = QPlainTextEdit(panel)
        self._diagnostics_view.setReadOnly(True)
        self._diagnostics_view.setMaximumBlockCount(200)

        features_label = QLabel("Features")
        self._feature_list = QListWidget(panel)

        operations_label = QLabel("Roughing Operations")
        self._operation_list = QListWidget(panel)
        self._operation_list.currentItemChanged.connect(self._on_operation_item_changed)

        details_label = QLabel("Selected Operation")
        self._operation_details_view = QPlainTextEdit(panel)
        self._operation_details_view.setReadOnly(True)
        self._operation_details_view.setMaximumBlockCount(400)

        panel_layout.addWidget(diagnostics_label)
        panel_layout.addWidget(self._diagnostics_view, 1)
        panel_layout.addWidget(features_label)
        panel_layout.addWidget(self._feature_list, 2)
        panel_layout.addWidget(operations_label)
        panel_layout.addWidget(self._operation_list, 2)
        panel_layout.addWidget(details_label)
        panel_layout.addWidget(self._operation_details_view, 3)

        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        container = QWidget(self)
        container_layout = QHBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(splitter)
        self.setCentralWidget(container)

    def _load_data(self):
        original_features = self.features
        self.features = []
        try:
            super()._load_data()
        finally:
            self.features = original_features
        self._refresh_benchmark_sidebar()
        self._update_benchmark_status()

    def _cycle_toolpath_filter(self, step: int):
        del step
        if self._selected_operation_id is not None:
            self._selected_operation_id = None
            if hasattr(self, "_operation_list"):
                self._operation_list.blockSignals(True)
                self._operation_list.clearSelection()
                self._operation_list.setCurrentItem(None)
                self._operation_list.blockSignals(False)
            self._operation_details_view.setPlainText(self._build_default_operation_details_text())
            self._display_toolpath_plan()
            self._update_benchmark_status("Benchmark roughing reset: all roughing operations visible")
            return

        self._update_benchmark_status("Benchmark viewer shows roughing only; select an operation to isolate it")

    def _should_display_toolpath_operation(self, operation) -> bool:
        if self._toolpath_operation_mode(operation) != "roughing":
            return False
        if self._selected_operation_id is None:
            return True
        return getattr(operation, "op_id", None) == self._selected_operation_id

    def _benchmark_toolpath_legend_html(self) -> str:
        catalog = self._toolpath_style_catalog()
        legend_keys = (
            "slot_roughing",
            "cavity_roughing",
            "profile_roughing",
            "roughing",
        )
        parts = []
        for key in legend_keys:
            entry = catalog[key]
            color = self._toolpath_color_hex(entry["color"])
            parts.append(f"<span style='color:{color}; font-weight:600'>{entry['label']}</span>")
        return "Roughing: " + " | ".join(parts)

    def _refresh_benchmark_sidebar(self) -> None:
        if hasattr(self, "_diagnostics_view"):
            self._diagnostics_view.setPlainText(self._build_diagnostics_text())
        if hasattr(self, "_feature_list"):
            self._populate_feature_list()
        if hasattr(self, "_operation_list"):
            self._populate_operation_list()
        if hasattr(self, "_operation_details_view") and self._selected_operation_id is None:
            self._operation_details_view.setPlainText(self._build_default_operation_details_text())

    def _populate_feature_list(self) -> None:
        self._feature_list.clear()
        for line in self._build_feature_summary_lines():
            self._feature_list.addItem(line)

    def _populate_operation_list(self) -> None:
        self._operation_list.blockSignals(True)
        self._operation_list.clear()

        roughing_operations = self._build_roughing_operation_summaries(
            getattr(self._toolpath_plan, "operations", []) if self._toolpath_plan is not None else []
        )
        self._roughing_summary_by_id = {summary.op_id: summary for summary in roughing_operations}
        self._roughing_operations_by_id = {
            getattr(operation, "op_id", ""): operation
            for operation in getattr(self._toolpath_plan, "operations", [])
            if self._toolpath_operation_mode(operation) == "roughing"
        }

        for summary in roughing_operations:
            item = QListWidgetItem(self._operation_summary_label(summary))
            item.setData(Qt.UserRole, summary.op_id)
            self._operation_list.addItem(item)

        self._operation_list.blockSignals(False)

    def _on_operation_item_changed(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        del previous
        self._selected_operation_id = current.data(Qt.UserRole) if current is not None else None
        if self._selected_operation_id is None:
            self._operation_details_view.setPlainText(self._build_default_operation_details_text())
        else:
            self._operation_details_view.setPlainText(self._build_selected_operation_text(self._selected_operation_id))
        self._display_toolpath_plan()
        if getattr(self.ocp_widget, "view", None) is not None:
            self.ocp_widget.view.Redraw()
        self._update_benchmark_status()

    def _update_benchmark_status(self, message: Optional[str] = None) -> None:
        if message is not None:
            self.statusBar().showMessage(message)
            return

        roughing_count = len(self._roughing_summary_by_id)
        if self._selected_operation_id is None:
            self.statusBar().showMessage(
                f"Benchmark roughing | operations={roughing_count} | select an operation in the sidebar to isolate it | T resets selection"
            )
            return

        summary = self._roughing_summary_by_id.get(self._selected_operation_id)
        if summary is None:
            self.statusBar().showMessage(f"Benchmark roughing | operations={roughing_count}")
            return

        self.statusBar().showMessage(
            f"Benchmark roughing | selected={summary.op_id} | strategy={summary.strategy} | style={summary.style} | passes={summary.pass_count} | paths={summary.path_count}"
        )

    def _build_diagnostics_text(self) -> str:
        diagnostics = self._benchmark_diagnostics or {}
        raw_candidates = diagnostics.get("raw_candidates", {})
        feature_types = diagnostics.get("feature_types", {})
        warnings = list(getattr(self._toolpath_plan, "warnings", []) or [])
        roughing_summaries = self._build_roughing_operation_summaries(
            getattr(self._toolpath_plan, "operations", []) if self._toolpath_plan is not None else []
        )

        lines = [
            f"has_brep: {diagnostics.get('has_brep', 'unknown')}",
            f"skip_reason: {diagnostics.get('skip_reason') or '-'}",
            f"raw_candidates: cylinders={raw_candidates.get('cylinders', 0)}, cones={raw_candidates.get('cones', 0)}, arc_groups={raw_candidates.get('arc_groups', 0)}",
            f"feature_count: {diagnostics.get('feature_count', len(self.features))}",
            f"pre_group_feature_count: {diagnostics.get('pre_group_feature_count', '-')}",
            f"roughing_operations: {len(roughing_summaries)}",
        ]

        piece_projection_bounds = diagnostics.get("piece_projection_bounds") or {}
        if piece_projection_bounds:
            lines.append(
                "piece_projection_bounds: "
                f"top={piece_projection_bounds.get('top', '-')}, "
                f"bottom={piece_projection_bounds.get('bottom', '-')}, "
                f"depth={piece_projection_bounds.get('depth', '-')}"
            )

        if feature_types:
            counts = ", ".join(f"{name}={count}" for name, count in sorted(feature_types.items()))
            lines.append(f"feature_types: {counts}")

        planner = self._benchmark_planner_config
        if planner is not None:
            lines.extend(
                [
                    f"planner_mode: {planner.parameter_mode}",
                    f"tool_library: {planner.tool_library}",
                    f"material_profile: {planner.material_profile}",
                    f"tool_diameter: {planner.tool_diameter:.3f}",
                    f"profile_stock_allowance: {planner.profile_stock_allowance:.3f}",
                ]
            )

        if warnings:
            lines.append("")
            lines.append("warnings:")
            lines.extend(f"- {warning}" for warning in warnings)

        return "\n".join(lines)

    def _build_feature_summary_lines(self) -> list[str]:
        if getattr(self._benchmark_planner_config, "piece_roughing_only", False):
            return ["Semantic features disabled in piece-based roughing mode"]

        lines: list[str] = []
        for index, feature in enumerate(self.features):
            props = getattr(feature, "props", {}) or {}
            metrics: list[str] = []
            for key in ("depth", "width", "length", "diameter"):
                value = props.get(key)
                if value is None:
                    continue
                metrics.append(f"{key}={self._format_number(value)}")
            if "through" in props:
                metrics.append(f"through={bool(props.get('through'))}")
            if getattr(feature, "type", "") == "hole_group":
                hole_count = len(getattr(feature, "holes", []) or [])
                metrics.append(f"holes={hole_count}")
            metrics_text = " | ".join(metrics) if metrics else "no numeric props"
            lines.append(f"{index:02d}. {getattr(feature, 'type', 'unknown')} | {metrics_text}")
        return lines or ["No extracted features"]

    def _build_default_operation_details_text(self) -> str:
        summaries = list(self._roughing_summary_by_id.values())
        if not summaries:
            return "No roughing operations available"

        total_cut_length = sum(summary.cut_length for summary in summaries)
        estimated_minutes = sum(summary.estimated_cut_minutes or 0.0 for summary in summaries)
        return "\n".join(
            [
                "Select a roughing operation from the sidebar to isolate it.",
                "",
                f"roughing_operations: {len(summaries)}",
                f"total_cut_length: {self._format_number(total_cut_length)} mm",
                f"estimated_cut_time: {self._format_number(estimated_minutes)} min",
            ]
        )

    def _build_selected_operation_text(self, op_id: str) -> str:
        summary = self._roughing_summary_by_id.get(op_id)
        operation = self._roughing_operations_by_id.get(op_id)
        if summary is None or operation is None:
            return self._build_default_operation_details_text()

        metadata = getattr(operation, "metadata", {}) or {}
        lines = [
            f"op_id: {summary.op_id}",
            f"strategy: {summary.strategy}",
            f"feature_type: {summary.feature_type}",
            f"style: {summary.style}",
            f"machining_class: {summary.machining_class}",
            f"feature_subtype: {summary.feature_subtype}",
            f"geometry_source: {summary.geometry_source}",
            f"pass_count: {summary.pass_count}",
            f"path_count: {summary.path_count}",
            f"motion_count: {summary.motion_count}",
            f"cut_segments: {summary.cut_segment_count}",
            f"cut_length: {self._format_number(summary.cut_length)} mm",
            f"estimated_cut_time: {self._format_optional_number(summary.estimated_cut_minutes, 'min')}",
            f"tool_id: {summary.tool_id or '-'}",
            f"tool_diameter: {self._format_optional_number(summary.tool_diameter, 'mm')}",
            f"cut_feed: {self._format_optional_number(summary.cut_feed, 'mm/min')}",
            f"plunge_feed: {self._format_optional_number(summary.plunge_feed, 'mm/min')}",
            "",
            "metadata:",
        ]
        for key, value in sorted(metadata.items()):
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)

    def _build_roughing_operation_summaries(self, operations) -> list[RoughingOperationSummary]:
        return [
            self._summarize_roughing_operation(operation)
            for operation in operations
            if self._toolpath_operation_mode(operation) == "roughing"
        ]

    def _summarize_roughing_operation(self, operation) -> RoughingOperationSummary:
        metadata = getattr(operation, "metadata", {}) or {}
        cut_paths = self._split_toolpath_motion_paths(getattr(operation, "motions", []), cut_feed=metadata.get("cut_feed"))
        cut_length = sum(self._path_length(path) for path in cut_paths)
        cut_feed = self._coerce_float(metadata.get("cut_feed"))
        estimated_cut_minutes = None if not cut_feed or cut_feed <= 1e-9 else cut_length / cut_feed
        path_count = self._coerce_int(
            metadata.get("roughing_path_count", metadata.get("path_count", metadata.get("line_count", len(cut_paths))))
        )
        style = str(
            metadata.get("roughing_style")
            or metadata.get("clearing_style")
            or metadata.get("boundary_style")
            or "-"
        )
        return RoughingOperationSummary(
            op_id=str(getattr(operation, "op_id", "")),
            strategy=str(getattr(operation, "strategy", "")),
            feature_type=str(getattr(operation, "feature_type", "")),
            style=style,
            pass_count=self._coerce_int(metadata.get("pass_count", 0)),
            path_count=path_count,
            tool_id=str(metadata.get("tool_id") or ""),
            tool_diameter=self._coerce_float(metadata.get("tool_diameter")) or 0.0,
            cut_feed=cut_feed,
            plunge_feed=self._coerce_float(metadata.get("plunge_feed")),
            motion_count=len(getattr(operation, "motions", [])),
            cut_segment_count=sum(max(len(path) - 1, 0) for path in cut_paths),
            cut_length=cut_length,
            estimated_cut_minutes=estimated_cut_minutes,
            machining_class=str(metadata.get("machining_class") or ""),
            feature_subtype=str(metadata.get("feature_subtype") or ""),
            geometry_source=str(metadata.get("geometry_source") or ""),
        )

    @staticmethod
    def _path_length(path: list[np.ndarray]) -> float:
        if len(path) < 2:
            return 0.0
        return float(
            sum(np.linalg.norm(np.array(path[index], dtype=float) - np.array(path[index - 1], dtype=float)) for index in range(1, len(path)))
        )

    @staticmethod
    def _operation_summary_label(summary: RoughingOperationSummary) -> str:
        return (
            f"{summary.op_id} | {summary.strategy} | {summary.style} | "
            f"passes={summary.pass_count} | paths={summary.path_count} | tool={summary.tool_id or '-'}"
        )

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _format_number(value: Any) -> str:
        try:
            return f"{float(value):.3f}"
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _format_optional_number(cls, value: Any, unit: str) -> str:
        numeric = cls._coerce_float(value)
        if numeric is None:
            return "-"
        return f"{cls._format_number(numeric)} {unit}"


def show_roughing_benchmark(
    model,
    features,
    working_plane_normal=(0.0, 0.0, 1.0),
    contour_shadow=None,
    perimeter=None,
    vertical_arc_groups=None,
    toolpath_plan=None,
    diagnostics: Optional[dict[str, Any]] = None,
    planner_config: Optional[Any] = None,
):
    app = QApplication.instance() or QApplication(sys.argv)
    window = RoughingBenchmarkWindow(
        model,
        features,
        working_plane_normal=working_plane_normal,
        contour_shadow=contour_shadow,
        perimeter=perimeter,
        vertical_arc_groups=vertical_arc_groups,
        toolpath_plan=toolpath_plan,
        diagnostics=diagnostics,
        planner_config=planner_config,
    )
    window.show()
    sys.exit(app.exec())