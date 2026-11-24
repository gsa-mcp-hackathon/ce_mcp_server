"""Loader errors. Each one stops that file's load and is recorded as `failed`."""


class CeIngestError(Exception):
    """Base class for every expected loader failure."""


class FilenameError(CeIngestError):
    """The file name or its folder doesn't match the PUMD layout."""


class SasReadError(CeIngestError):
    """The SAS file couldn't be read, or its row count differs from the header."""


class ColumnCollisionError(CeIngestError):
    """Two columns would end up with the same name."""


class MissingKeyError(CeIngestError):
    """No usable `newid` / `cuid` key."""


class QuarterError(CeIngestError):
    """A `qyear` is null or outside the release's five quarters."""


class TypeChangeError(CeIngestError):
    """A column's type differs from the type already stored for the table."""


class WriteError(CeIngestError):
    """Staging or swapping a partition failed; the live partition is untouched."""
