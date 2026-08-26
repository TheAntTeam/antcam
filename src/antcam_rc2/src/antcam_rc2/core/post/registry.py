"""Explicit deterministic registry of post-processors (keyed by post id)."""

from __future__ import annotations

from collections.abc import Iterable

from antcam_rc2.core.errors import PostProcessorError
from antcam_rc2.core.post.base import BasePostProcessor


class PostProcessorRegistry:
    """Maps post ids to post-processor classes; injected, no global state."""

    def __init__(self, posts: Iterable[type[BasePostProcessor]] = ()) -> None:
        self._posts: dict[str, type[BasePostProcessor]] = {}
        for post in posts:
            self.register(post)

    def register(self, post: type[BasePostProcessor]) -> None:
        if post.post_id in self._posts:
            raise PostProcessorError(f"post already registered: {post.post_id}")
        self._posts[post.post_id] = post

    def get(self, post_id: str) -> BasePostProcessor:
        try:
            return self._posts[post_id]()
        except KeyError as exc:
            raise PostProcessorError(f"post not registered: {post_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._posts))


def build_standard_posts() -> PostProcessorRegistry:
    """The canonical registry with the GRBL, LinuxCNC and Makera posts."""
    from antcam_rc2.core.post.grbl import GrblPost
    from antcam_rc2.core.post.linuxcnc import LinuxCncPost
    from antcam_rc2.core.post.makera import MakeraPost

    return PostProcessorRegistry((GrblPost, LinuxCncPost, MakeraPost))


__all__ = ["PostProcessorRegistry", "build_standard_posts"]
