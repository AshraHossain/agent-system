"""Typed tool errors. Graph nodes map these to ``ErrorRecord``s with a predictable ``kind``."""

from __future__ import annotations


class ToolError(Exception):
    """Base class for every expected tool failure."""

    recoverable: bool = True


class ToolInputError(ToolError, ValueError):
    """The caller passed invalid input (unknown entity, bad window, malformed id)."""

    recoverable = False


class DataNotFoundError(ToolError, LookupError):
    """A dataset, entity or document does not exist."""

    recoverable = False


class DataCorruptError(ToolError):
    """Stored data failed validation (wrong columns, unparsable values)."""

    recoverable = False
