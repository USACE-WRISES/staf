"""The reactive-effect guard.

py-shiny isolates an exception raised inside a ``@render.*`` output to that one
output box, but an exception raised inside a ``@reactive.effect`` reaches
``Session._unhandled_error``, which closes the websocket. A StreamCurves project
lives only in session memory, so that path is not an error message, it is the
whole run gone. ``guard`` converts the second case into the first.

An AST sweep of the app at the time this was written found 172 reactive effects,
19 of which had a top-level try. The rest could each end a session.
"""
from __future__ import annotations

import asyncio

import pytest

from views import uihelpers as uh


@pytest.fixture
def toasts(monkeypatch):
    """Capture ui.notification_show without a Shiny session."""
    seen = []
    monkeypatch.setattr(
        uh.ui, "notification_show",
        lambda msg, **kw: seen.append({"message": str(msg), **kw}))
    return seen


# --------------------------------------------------------------------------- #
# The thing that matters: a raise does not escape
# --------------------------------------------------------------------------- #
def test_a_raising_body_does_not_propagate(toasts):
    """If this ever fails, the exception reaches Shiny and the session closes."""

    @uh.guard("save the mapping")
    def handler():
        raise ValueError("workbook is locked")

    assert handler() is None


def test_the_message_names_the_action_and_the_error(toasts):
    @uh.guard("save the mapping")
    def handler():
        raise ValueError("workbook is locked")

    handler()
    assert len(toasts) == 1
    assert toasts[0]["message"] == "Could not save the mapping: workbook is locked"
    assert toasts[0]["type"] == "error"


def test_even_a_baseexception_subclass_users_can_trigger_is_caught(toasts):
    """KeyError from a missing config key is the shape most of these take."""

    @uh.guard("open the picker")
    def handler():
        {}["curve_stratification"]

    assert handler() is None
    assert "curve_stratification" in toasts[0]["message"]


# --------------------------------------------------------------------------- #
# The success path is untouched
# --------------------------------------------------------------------------- #
def test_a_successful_body_is_passed_through_unchanged(toasts):
    @uh.guard("do the thing")
    def handler(a, b, *, c):
        return (a, b, c)

    assert handler(1, 2, c=3) == (1, 2, 3)
    assert toasts == []


def test_the_handler_keeps_its_own_name():
    """Shiny reads __name__ off the callable; losing it would rename outputs."""

    @uh.guard("do the thing")
    def _clear_mapping():
        return None

    assert _clear_mapping.__name__ == "_clear_mapping"


# --------------------------------------------------------------------------- #
# Async bodies: several effects in this app are async
# --------------------------------------------------------------------------- #
def test_an_async_body_still_awaits_to_its_result(toasts):
    @uh.guard("compile the data")
    async def handler():
        await asyncio.sleep(0)
        return "done"

    assert asyncio.run(handler()) == "done"
    assert toasts == []


def test_an_async_body_that_raises_is_caught_too(toasts):
    @uh.guard("compile the data")
    async def handler():
        await asyncio.sleep(0)
        raise RuntimeError("source unreachable")

    assert asyncio.run(handler()) is None
    assert toasts[0]["message"] == "Could not compile the data: source unreachable"


def test_an_async_body_stays_a_coroutine_function():
    """reactive.effect inspects this to decide how to invoke the handler."""

    @uh.guard("compile the data")
    async def handler():
        return None

    assert asyncio.iscoroutinefunction(handler)


# --------------------------------------------------------------------------- #
# Shiny's control flow must pass straight through.
#
# req() and reading an input that is not set yet raise SilentException to stop an
# effect quietly. Swallowing that turns "not set yet" into a red error toast --
# the live server log caught exactly that on _mirror_curves_section before this
# was added.
# --------------------------------------------------------------------------- #
def test_silentexception_is_not_treated_as_a_failure(toasts):
    from shiny.types import SilentException

    @uh.guard("track the section")
    def handler():
        raise SilentException()

    with pytest.raises(SilentException):
        handler()
    assert toasts == [], "req() must not produce an error toast"


def test_silentcanceloutput_passes_through_too(toasts):
    from shiny.types import SilentCancelOutputException

    @uh.guard("track the section")
    def handler():
        raise SilentCancelOutputException()

    with pytest.raises(SilentCancelOutputException):
        handler()
    assert toasts == []


def test_notifyexception_keeps_its_own_semantics(toasts):
    """It carries its own message and its own decision about closing."""
    from shiny.types import NotifyException

    @uh.guard("save the mapping")
    def handler():
        raise NotifyException("region is locked")

    with pytest.raises(NotifyException):
        handler()
    assert toasts == []


def test_control_flow_passes_through_async_bodies_as_well(toasts):
    from shiny.types import SilentException

    @uh.guard("compile the data")
    async def handler():
        await asyncio.sleep(0)
        raise SilentException()

    with pytest.raises(SilentException):
        asyncio.run(handler())
    assert toasts == []
