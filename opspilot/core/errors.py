"""Explicit error types for tools. ADK wrappers turn these into error results."""


class ToolError(Exception):
    """Base class for expected, reportable tool failures."""

    code = "tool_error"


class InvalidArgument(ToolError):
    code = "invalid_argument"


class NotFound(ToolError):
    code = "not_found"


class DataUnavailable(ToolError):
    code = "data_unavailable"
