"""Feature tree model: hierarchical view of stock, features and their operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt

from antcam_rc2.core.geometry3d.scene import Feature3D, FeatureKind
from antcam_rc2.core.project.models import Operation, Project


class FeatureNodeKind(Enum):
    """Type of node in the feature tree."""

    STOCK = "stock"
    FEATURE = "feature"
    OPERATION = "operation"


@dataclass(frozen=True)
class FeatureTreeNode:
    """Immutable node in the feature tree."""

    kind: FeatureNodeKind
    # For STOCK: no extra data needed
    # For FEATURE: body_index, feature_index, feature (3D) or layer, entity_index, entity_type (2D)
    # For OPERATION: operation_id
    body_index: int = -1
    feature_index: int = -1
    feature: Feature3D | None = None
    layer: str | None = None
    entity_index: int = -1
    entity_type: str | None = None
    operation_id: str | None = None

    def __str__(self) -> str:
        if self.kind == FeatureNodeKind.STOCK:
            return "Stock"
        elif self.kind == FeatureNodeKind.FEATURE:
            if self.feature is not None:
                kind = self.feature.kind.value
                z = self.feature.plane_z_mm
                detail = ""
                if self.feature.kind == FeatureKind.HOLE and self.feature.radius is not None:
                    detail = f" r={self.feature.radius:.2f}mm"
                elif self.feature.boundary:
                    detail = f" verts={len(self.feature.boundary)}"
                facing = " ↑" if self.feature.facing else ""
                return f"{kind} [{self.body_index}:{self.feature_index}] z={z:.2f}{detail}{facing}"
            elif self.layer is not None:
                return f"{self.entity_type} [{self.layer}:{self.entity_index}]"
            return "Feature"
        elif self.kind == FeatureNodeKind.OPERATION:
            return f"Operation ({self.operation_id})"
        return "Node"


def build_feature_nodes(
    solid_scene: object | None = None,
    scene: object | None = None,
) -> list[FeatureTreeNode]:
    """Build the feature nodes (children of Stock) from solid_scene and 2D scene."""
    from antcam_rc2.core.geometry3d.scene import SolidScene
    from antcam_rc2.core.io.scene import GeometryScene

    feature_nodes: list[FeatureTreeNode] = []

    # 3D Features from SolidScene
    if solid_scene is not None and isinstance(solid_scene, SolidScene):
        for body_index, body in enumerate(solid_scene.bodies):
            for feature_index, feature in enumerate(body.features):
                # Only Z-up features are machinable
                if feature.facing:
                    feature_node = FeatureTreeNode(
                        kind=FeatureNodeKind.FEATURE,
                        body_index=body_index,
                        feature_index=feature_index,
                        feature=feature,
                    )
                    feature_nodes.append(feature_node)

    # 2D Geometry entities from GeometryScene
    if scene is not None and isinstance(scene, GeometryScene):
        for layer in scene.layers:
            if not layer.entities:
                continue
            for entity_index, entity in enumerate(layer.entities):
                entity_type = type(entity).__name__
                feature_node = FeatureTreeNode(
                    kind=FeatureNodeKind.FEATURE,
                    layer=layer.name,
                    entity_index=entity_index,
                    entity_type=entity_type,
                )
                feature_nodes.append(feature_node)

    return feature_nodes


class FeatureTreeModel(QAbstractItemModel):
    """Qt model for the feature tree view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._feature_nodes: list[FeatureTreeNode] = []
        # Operation child nodes are handed to ``createIndex`` as the internal
        # pointer, so the model MUST keep them alive for as long as the index
        # can be used.  Creating them on the fly returned a dangling pointer and
        # crashed the view with a segfault as soon as a feature had operations.
        self._operation_nodes: dict[str, FeatureTreeNode] = {}
        self._project: Project | None = None
        self._stock_node = FeatureTreeNode(kind=FeatureNodeKind.STOCK)

    def set_data(
        self,
        project: Project | None,
        solid_scene: object | None = None,
        scene: object | None = None,
    ) -> None:
        """Update the model with new data."""
        self.beginResetModel()
        self._project = project
        self._feature_nodes = build_feature_nodes(solid_scene, scene)
        self._operation_nodes.clear()
        self.endResetModel()

    def index(self, row: int, column: int, parent: QModelIndex | None = None) -> QModelIndex:
        if parent is None:
            parent = QModelIndex()
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            # Root level: Stock node at row 0
            if row == 0:
                return self.createIndex(row, column, self._stock_node)
            return QModelIndex()

        parent_node = parent.internalPointer()
        if not isinstance(parent_node, FeatureTreeNode):
            return QModelIndex()

        if parent_node.kind == FeatureNodeKind.STOCK:
            # Children of Stock are feature nodes
            if row < len(self._feature_nodes):
                return self.createIndex(row, column, self._feature_nodes[row])
            return QModelIndex()

        if parent_node.kind == FeatureNodeKind.FEATURE:
            # Children of Feature are operation nodes
            child = self._operation_child(parent_node, row)
            if child is not None:
                return self.createIndex(row, column, child)
            return QModelIndex()

        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        node = index.internalPointer()
        if not isinstance(node, FeatureTreeNode):
            return QModelIndex()

        if node.kind == FeatureNodeKind.STOCK:
            return QModelIndex()

        if node.kind == FeatureNodeKind.FEATURE:
            # Parent of Feature is Stock
            return self.createIndex(0, 0, self._stock_node)

        if node.kind == FeatureNodeKind.OPERATION:
            # Parent of Operation is its Feature
            feature_node = self._feature_of_operation(node)
            if feature_node is not None:
                # Find row of this feature in _feature_nodes
                for i, fn in enumerate(self._feature_nodes):
                    if self._same_feature(fn, feature_node):
                        return self.createIndex(i, 0, fn)
            return QModelIndex()

        return QModelIndex()

    def index_for_operation(self, operation_id: str) -> QModelIndex:
        """Return the index of the operation node anywhere in the tree."""
        stock = self.index(0, 0, QModelIndex())
        for row, feature_node in enumerate(self._feature_nodes):
            operations = self._operations_for_feature(feature_node)
            for operation_row, operation in enumerate(operations):
                if operation.id == operation_id:
                    return self.index(operation_row, 0, self.index(row, 0, stock))
        return QModelIndex()

    def index_for_feature(
        self,
        *,
        body_index: int = -1,
        feature_index: int = -1,
        layer: str | None = None,
        entity_index: int = -1,
    ) -> QModelIndex:
        """Return the index of a feature node (3D feature or 2D entity)."""
        stock = self.index(0, 0, QModelIndex())
        for row, node in enumerate(self._feature_nodes):
            if node.feature is not None and node.body_index == body_index and node.feature_index == feature_index:
                return self.index(row, 0, stock)
            if layer is not None and node.layer == layer and node.entity_index == entity_index:
                return self.index(row, 0, stock)
        return QModelIndex()

    def _same_feature(self, a: FeatureTreeNode, b: FeatureTreeNode) -> bool:
        """Check if two feature nodes represent the same feature."""
        if a.feature is not None and b.feature is not None:
            return a.body_index == b.body_index and a.feature_index == b.feature_index
        if a.layer is not None and b.layer is not None:
            return a.layer == b.layer and a.entity_index == b.entity_index
        return False

    def _feature_of_operation(self, op_node: FeatureTreeNode) -> FeatureTreeNode | None:
        """Find the feature node that owns this operation."""
        if op_node.operation_id is None or self._project is None:
            return None
        operation = next((op for op in self._project.operations if op.id == op_node.operation_id), None)
        if operation is None:
            return None

        # Check solid_refs first
        for ref in operation.solid_refs:
            for fn in self._feature_nodes:
                if fn.feature is not None and fn.body_index == ref.body_index and fn.feature_index == ref.feature_index:
                    return fn
        # Check geometry_refs
        for ref in operation.geometry_refs:
            for fn in self._feature_nodes:
                if fn.layer == ref.layer_name and fn.entity_index == ref.entity_index:
                    return fn
        return None

    def _operation_child(self, feature_node: FeatureTreeNode, row: int) -> FeatureTreeNode | None:
        """Get operation child at row for a feature node."""
        if self._project is None:
            return None

        ops = self._operations_for_feature(feature_node)
        if row < len(ops):
            operation_id = ops[row].id
            node = self._operation_nodes.get(operation_id)
            if node is None:
                node = FeatureTreeNode(kind=FeatureNodeKind.OPERATION, operation_id=operation_id)
                self._operation_nodes[operation_id] = node
            return node
        return None

    def _operations_for_feature(self, feature_node: FeatureTreeNode) -> list[Operation]:
        """Get all operations assigned to this feature."""
        if self._project is None:
            return []

        ops = []
        for operation in self._project.operations:
            # Check solid_refs
            for ref in operation.solid_refs:
                if (
                    feature_node.feature is not None
                    and feature_node.body_index == ref.body_index
                    and feature_node.feature_index == ref.feature_index
                ):
                    ops.append(operation)
                    break
            # Check geometry_refs
            for ref in operation.geometry_refs:
                if (
                    feature_node.layer is not None
                    and feature_node.layer == ref.layer_name
                    and feature_node.entity_index == ref.entity_index
                ):
                    ops.append(operation)
                    break
        return ops

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            return 1  # Only Stock at root

        node = parent.internalPointer()
        if not isinstance(node, FeatureTreeNode):
            return 0

        if node.kind == FeatureNodeKind.STOCK:
            return len(self._feature_nodes)

        if node.kind == FeatureNodeKind.FEATURE:
            return len(self._operations_for_feature(node))

        return 0

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None

        node = index.internalPointer()
        if not isinstance(node, FeatureTreeNode):
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return str(node)
        if role == Qt.ItemDataRole.ToolTipRole:
            if node.kind == FeatureNodeKind.FEATURE and node.feature is not None:
                kind = node.feature.kind.value
                z = node.feature.plane_z_mm
                return f"Body {node.body_index} — {kind} z={z:.2f}"
            return str(node)
        if role == Qt.ItemDataRole.UserRole:
            return node
        if role == Qt.ItemDataRole.CheckStateRole and node.kind == FeatureNodeKind.OPERATION:
            if self._project is not None:
                operation = next((op for op in self._project.operations if op.id == node.operation_id), None)
                if operation is not None:
                    return Qt.CheckState.Checked if operation.enabled else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole:
            if node.kind == FeatureNodeKind.STOCK:
                return "📦"
            elif node.kind == FeatureNodeKind.FEATURE:
                if node.feature is not None:
                    if node.feature.kind == FeatureKind.HOLE:
                        return "🕳️"
                    elif node.feature.kind == FeatureKind.FACE_PLANAR:
                        return "📐"
                    elif node.feature.kind == FeatureKind.PERIMETER:
                        return "⬜"
                return "🔷"
            elif node.kind == FeatureNodeKind.OPERATION:
                return "⚙️"
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = index.internalPointer()
        if isinstance(node, FeatureTreeNode):
            if node.kind == FeatureNodeKind.OPERATION:
                return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
            return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return Qt.ItemFlag.NoItemFlags

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.isValid():
            node = index.internalPointer()
            if isinstance(node, FeatureTreeNode) and node.kind == FeatureNodeKind.OPERATION:
                if self._project is not None:
                    operation = next((op for op in self._project.operations if op.id == node.operation_id), None)
                    if operation is not None:
                        # The controller will handle the actual toggle via command
                        pass
        return False
