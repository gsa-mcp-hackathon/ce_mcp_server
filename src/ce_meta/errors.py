"""Errors of the metadata service.

Each one carries what a tool needs to answer usefully (closest names, loaded
years), so no tool has to return a stack trace.
"""


class CeMetaError(Exception):
    """Base class. `suggestions` is what the caller could try instead."""

    suggestions: list[str]

    def __init__(self, message: str, suggestions: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.suggestions = list(suggestions or [])


class UnknownVariable(CeMetaError):
    """No column has that stored name or BLS spelling."""

    def __init__(self, name: str, closest: list[str], table: str | None = None) -> None:
        where = f" in {table}" if table else ""
        super().__init__(f"No variable named {name!r}{where}.", closest)


class UnknownTable(CeMetaError):
    """No table has that stored name or sidecar id."""

    def __init__(self, name: str, closest: list[str]) -> None:
        super().__init__(f"No table named {name!r}.", closest)


class YearNotCovered(CeMetaError):
    """The year is outside what the service can describe."""

    def __init__(self, year: int, nearest: list[int], what: str | None = None) -> None:
        subject = (
            f"{what} has no entry for" if what else "The dictionary does not reach"
        )
        super().__init__(f"{subject} {year}.", [str(y) for y in nearest])


class OutOfScope(CeMetaError):
    """The name exists, but outside this service (a Diary variable)."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name!r} is outside this service: {reason}")


class CatalogMissing(CeMetaError):
    """A stored table is not built yet."""

    def __init__(self, area: str, name: str, command: str) -> None:
        super().__init__(f"The {area}/{name} table is not built yet. Run `{command}`.")
        self.area = area
        self.name = name
        self.command = command
