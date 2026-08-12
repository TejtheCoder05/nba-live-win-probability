"""Typed failures at the external live-data boundary."""


class LiveDataError(RuntimeError):
    """Base class for live endpoint and schema failures."""


class LiveEndpointError(LiveDataError):
    """The NBA endpoint could not return a usable response."""


class LiveSchemaError(LiveDataError):
    """A response did not satisfy the observed live-data contract."""
