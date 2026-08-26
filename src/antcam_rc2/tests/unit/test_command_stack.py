"""Tests for core.services.command_stack."""

from __future__ import annotations

import pytest

from antcam_rc2.core.services.command_stack import CommandStack
from antcam_rc2.core.services.commands import Command


class Counter:
    def __init__(self) -> None:
        self.value = 0


class IncrementCommand(Command):
    def __init__(self, counter: Counter) -> None:
        self.counter = counter

    @property
    def name(self) -> str:
        return "increment"

    def execute(self) -> None:
        self.counter.value += 1

    def undo(self) -> None:
        self.counter.value -= 1


class FailingUndoCommand(Command):
    def __init__(self, counter: Counter) -> None:
        self.counter = counter

    @property
    def name(self) -> str:
        return "failing-undo"

    def execute(self) -> None:
        self.counter.value += 1

    def undo(self) -> None:
        raise RuntimeError("cannot undo")


class FailingExecuteCommand(Command):
    def __init__(self, counter: Counter, *, fail_on: int) -> None:
        self.counter = counter
        self.fail_on = fail_on
        self.executions = 0

    @property
    def name(self) -> str:
        return "failing-execute"

    def execute(self) -> None:
        self.executions += 1
        if self.executions == self.fail_on:
            raise RuntimeError("cannot execute")
        self.counter.value += 1

    def undo(self) -> None:
        self.counter.value -= 1


def test_push_executes() -> None:
    counter = Counter()
    stack = CommandStack()
    stack.push(IncrementCommand(counter))
    assert counter.value == 1


def test_undo_redo() -> None:
    counter = Counter()
    stack = CommandStack()
    stack.push(IncrementCommand(counter))
    stack.push(IncrementCommand(counter))
    assert counter.value == 2

    assert stack.undo()
    assert counter.value == 1
    assert stack.can_redo

    assert stack.redo()
    assert counter.value == 2
    assert not stack.can_redo


def test_undo_empty_returns_false() -> None:
    stack = CommandStack()
    assert not stack.undo()


def test_redo_empty_returns_false() -> None:
    stack = CommandStack()
    assert not stack.redo()


def test_new_push_clears_redo() -> None:
    counter = Counter()
    stack = CommandStack()
    stack.push(IncrementCommand(counter))
    stack.undo()
    assert stack.can_redo
    stack.push(IncrementCommand(counter))
    assert not stack.can_redo


def test_max_depth_bounded() -> None:
    counter = Counter()
    stack = CommandStack(max_depth=2)
    for _ in range(5):
        stack.push(IncrementCommand(counter))
    assert stack.undo_count == 2


def test_max_depth_at_least_one() -> None:
    assert CommandStack(max_depth=0).max_depth == 1


def test_clear() -> None:
    counter = Counter()
    stack = CommandStack()
    stack.push(IncrementCommand(counter))
    stack.undo()
    stack.clear()
    assert not stack.can_undo
    assert not stack.can_redo


def test_on_change_callback() -> None:
    counter = Counter()
    stack = CommandStack()
    changes: list[str] = []
    stack.set_on_change(lambda: changes.append("changed"))
    stack.push(IncrementCommand(counter))
    stack.undo()
    stack.redo()
    stack.clear()
    assert changes == ["changed", "changed", "changed", "changed"]


def test_failed_undo_keeps_command_on_stack() -> None:
    counter = Counter()
    stack = CommandStack()
    stack.push(FailingUndoCommand(counter))
    assert stack.undo_count == 1
    with pytest.raises(RuntimeError, match="cannot undo"):
        stack.undo()
    assert stack.undo_count == 1
    assert not stack.can_redo


def test_failed_redo_keeps_command_on_stack() -> None:
    counter = Counter()
    stack = CommandStack()
    command = FailingExecuteCommand(counter, fail_on=2)
    stack.push(command)
    stack.undo()
    assert stack.redo_count == 1
    with pytest.raises(RuntimeError, match="cannot execute"):
        stack.redo()
    assert stack.redo_count == 1
    assert not stack.can_undo
    # The command remains retryable on the redo stack.
    assert stack.redo()
    assert counter.value == 1
