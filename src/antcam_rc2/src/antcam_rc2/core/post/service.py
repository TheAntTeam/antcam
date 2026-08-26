"""Post-processing service: selection, translation and atomic .nc export."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.errors import PostProcessorError, ProjectError
from antcam_rc2.core.post.base import BasePostProcessor
from antcam_rc2.core.post.models import (
    GCodeProgram,
    OperationMeta,
    PostContext,
    PostSettings,
    ToolEntry,
    ToolTable,
)
from antcam_rc2.core.post.registry import PostProcessorRegistry, build_standard_posts
from antcam_rc2.core.post.validator import validate
from antcam_rc2.core.project.models import Project
from antcam_rc2.core.toolpath.models import OperationPlanStatus, ToolpathPlan


class PostService:
    """Translates a validated plan into deterministic G-code for a machine."""

    def __init__(
        self,
        catalog_repository: CatalogRepository,
        registry: PostProcessorRegistry | None = None,
    ) -> None:
        self._catalogs = catalog_repository
        self._registry = registry or build_standard_posts()

    def post_ids(self) -> tuple[str, ...]:
        """Registered post ids (for UI/CLI selection)."""
        return self._registry.ids()

    def post(
        self,
        project: Project,
        plan: ToolpathPlan,
        settings: PostSettings | None = None,
        post_id: str | None = None,
    ) -> GCodeProgram:
        """Translate ``plan`` with the post selected by ``post_id`` (or the
        machine's ``native_post`` when omitted).  Raises on non-executable
        plans and on post-condition violations.
        """
        if not plan.is_executable:
            raise PostProcessorError("toolpath plan is not executable; refusing to post")
        settings = settings or PostSettings()
        if post_id is None:
            machine = self._catalogs.machine(project.machine_id)
            post_id = machine.native_post
        if post_id not in self._registry.ids():
            raise PostProcessorError(f"post not registered: {post_id}")
        post: BasePostProcessor = self._registry.get(post_id)

        context = self._build_context(project, plan, settings)
        program = post.convert(context)
        problems = validate(program)
        if problems:
            raise PostProcessorError("post-condition violations: " + "; ".join(problems[:5]))
        return program

    def export_nc(
        self,
        project: Project,
        plan: ToolpathPlan,
        destination: Path,
        settings: PostSettings | None = None,
        post_id: str | None = None,
    ) -> Path:
        """Atomically write the G-code file; returns the destination path."""
        program = self.post(project, plan, settings=settings, post_id=post_id)
        destination = Path(destination)
        if not destination.parent.is_dir():
            raise ProjectError(f"destination directory does not exist: {destination.parent}")
        text = program.text()
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, destination)
        except OSError as exc:
            raise ProjectError(f"failed to write G-code file: {destination}") from exc
        finally:
            if temp_name is not None:
                Path(temp_name).unlink(missing_ok=True)
        return destination

    # ------------------------------------------------------------------ internals
    def _build_context(self, project: Project, plan: ToolpathPlan, settings: PostSettings) -> PostContext:
        machine = self._catalogs.machine(project.machine_id)
        tool_table = _build_tool_table(project, plan, self._catalogs)
        operation_meta: dict[str, OperationMeta] = {}
        for result in plan.operations:
            if result.status is not OperationPlanStatus.SUCCEEDED or result.program is None:
                continue
            operation = next(candidate for candidate in project.operations if candidate.id == result.operation_id)
            rpm = result.feeds_speeds.rpm if result.feeds_speeds is not None else None
            operation_meta[result.operation_id] = OperationMeta(
                operation_id=result.operation_id,
                tool_id=operation.tool_id,
                name=operation.name,
                rpm=rpm,
                cooling_id=operation.cooling_id,
            )
        return PostContext(
            project=project,
            plan=plan,
            settings=settings,
            machine=machine,
            tool_table=tool_table,
            operation_meta=operation_meta,
            plan_fingerprint=plan.fingerprint(),
        )


def _build_tool_table(
    project: Project,
    plan: ToolpathPlan,
    catalogs: CatalogRepository,
) -> ToolTable:
    entries: list[ToolEntry] = []
    seen: dict[str, int] = {}
    for result in plan.operations:
        if result.status is not OperationPlanStatus.SUCCEEDED or result.program is None:
            continue
        operation = next(candidate for candidate in project.operations if candidate.id == result.operation_id)
        if operation.tool_id in seen:
            continue
        tool = catalogs.tool(operation.tool_id)
        seen[operation.tool_id] = len(entries) + 1
        entries.append(
            ToolEntry(
                tool_id=operation.tool_id,
                tool_number=len(entries) + 1,
                name=tool.name,
                diameter_mm=tool.cutting_diameter_mm,
            )
        )
    return ToolTable(entries=tuple(entries))


__all__ = ["PostService"]
