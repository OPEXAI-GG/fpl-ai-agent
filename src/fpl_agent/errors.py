"""Typed failures for external sources, so callers can skip a source safely instead of crashing."""


class SourceError(Exception):
    """A data source could not be used. Callers skip the source and alert."""


class SourceUnavailable(SourceError):
    """Network failure, 5xx, rate limit or the game being updated."""


class SourceFormatError(SourceError):
    """The response arrived but is not the shape we expect (site changed, HTML instead of JSON)."""
