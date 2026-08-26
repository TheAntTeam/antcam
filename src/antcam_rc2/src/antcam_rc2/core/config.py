"""Application configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_DEFAULT_USER_DATA_DIR = Path.home() / ".antcam_rc2"


class AppConfig(BaseSettings):
    """Runtime configuration for AntCAM RC2.

    All fields can be overridden through environment variables prefixed with
    ``ANTCAM_`` (e.g. ``ANTCAM_LOG_LEVEL``).
    """

    model_config = SettingsConfigDict(env_prefix="ANTCAM_", extra="ignore")

    log_level: LogLevelName = "INFO"
    log_file: Path | None = None
    user_data_dir: Path = _DEFAULT_USER_DATA_DIR
    cache_dir: Path | None = None

    @field_validator("log_file", "user_data_dir", "cache_dir", mode="before")
    @classmethod
    def _expand_path(cls, value: object) -> object:
        if isinstance(value, (str, Path)):
            return Path(value).expanduser()
        return value

    @model_validator(mode="after")
    def _default_cache_dir(self) -> AppConfig:
        if self.cache_dir is None:
            self.cache_dir = self.user_data_dir / "cache"
        return self


def load_config() -> AppConfig:
    """Load configuration from environment, falling back to defaults."""
    return AppConfig()
