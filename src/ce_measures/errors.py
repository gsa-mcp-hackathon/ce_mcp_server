"""Errors of request processing. A blocked node is a verdict, never an error."""

from __future__ import annotations


class CeMeasuresError(Exception):
    """Base class; `message` is safe to show to a client."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotProcessable(CeMeasuresError):
    """The request is not captured, or has no resolved record."""

    def __init__(self, message: str, status: str | None, reason: str | None) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


class PairMismatch(CeMeasuresError):
    """The request and resolved records do not list the same nodes."""


class UnknownPath(CeMeasuresError):
    """A rule's path names something a node cannot have: a rule-table mistake."""


class UnknownMethod(CeMeasuresError):
    """A verdict names a method no measure module has."""


class DataMissing(CeMeasuresError):
    """A table the node needs has no stored rows."""
