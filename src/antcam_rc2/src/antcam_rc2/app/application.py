"""Application bootstrap: wires the kernel services together."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from antcam_rc2.app.container import Container
from antcam_rc2.app.event_bus import EventBus
from antcam_rc2.core.config import AppConfig, load_config
from antcam_rc2.core.databases.repository import CatalogRepository
from antcam_rc2.core.feeds_speeds.calculator import FeedsSpeedsCalculator
from antcam_rc2.core.logging import setup_logger
from antcam_rc2.core.operations.registry import build_standard_registry
from antcam_rc2.core.project.repository import InMemoryProjectRepository
from antcam_rc2.core.services.command_stack import CommandStack
from antcam_rc2.core.services.project_service import ProjectService
from antcam_rc2.core.toolpath.service import ToolpathService

_SERVICE_EVENT_BUS = "event_bus"
_SERVICE_COMMAND_STACK = "command_stack"
_SERVICE_PROJECT = "project_service"
_SERVICE_CATALOGS = "catalog_repository"
_SERVICE_TOOLPATH = "toolpath_service"

_LEVELS = logging.getLevelNamesMapping()


class Application:
    """Root application object.

    Creates the dependency container, registers the core services and provides
    accessors for the UI frontend. Call :meth:`shutdown` to release resources.
    """

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or load_config()
        self.container = Container()

        level_name = self.config.log_level
        self.logger = setup_logger(
            level=_LEVELS[level_name],
            log_file=self.config.log_file,
        )

        event_bus = EventBus()
        command_stack = CommandStack()
        catalog_repository = CatalogRepository.from_package()
        project_service = ProjectService(
            project_repository=InMemoryProjectRepository(),
            catalog_repository=catalog_repository,
            command_stack=command_stack,
            event_bus=event_bus,
            clock=load_current_utc,
        )
        toolpath_service = ToolpathService(
            project_service=project_service,
            catalog_repository=catalog_repository,
            registry=build_standard_registry(),
            feeds_speeds=FeedsSpeedsCalculator(),
        )

        self.container.register(_SERVICE_EVENT_BUS, lambda: event_bus)
        self.container.register(_SERVICE_COMMAND_STACK, lambda: command_stack)
        self.container.register(_SERVICE_PROJECT, lambda: project_service)
        self.container.register(_SERVICE_CATALOGS, lambda: catalog_repository)
        self.container.register(_SERVICE_TOOLPATH, lambda: toolpath_service)

        self._shutdown = False

    @property
    def services(self) -> Container:
        """Container exposing resolved kernel services to the UI."""
        return self.container

    @property
    def event_bus(self) -> EventBus:
        """The application-wide event bus."""
        return self.container.resolve(_SERVICE_EVENT_BUS)

    @property
    def command_stack(self) -> CommandStack:
        """The application-wide undo/redo stack."""
        return self.container.resolve(_SERVICE_COMMAND_STACK)

    @property
    def project_service(self) -> ProjectService:
        """The command-backed service for project setup state."""
        return self.container.resolve(_SERVICE_PROJECT)

    @property
    def catalog_repository(self) -> CatalogRepository:
        """The lazily loaded read-only machining catalog repository."""
        return self.container.resolve(_SERVICE_CATALOGS)

    @property
    def toolpath_service(self) -> ToolpathService:
        """The headless Phase 4 service for neutral toolpath planning."""
        return self.container.resolve(_SERVICE_TOOLPATH)

    def shutdown(self) -> None:
        """Release application resources. Idempotent."""
        if self._shutdown:
            return
        self._shutdown = True
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)


def load_current_utc() -> datetime:
    """Return the current UTC timestamp for services that persist mutations."""
    return datetime.now(UTC)
