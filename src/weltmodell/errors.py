"""Fehlertypen des Substrats."""


class WeltmodellError(Exception):
    """Basisklasse."""


class RegistryError(WeltmodellError):
    """Verstoß gegen Registry-Regeln (Gate, §7.1)."""


class ValidationError(WeltmodellError):
    """Shape-Check-Reject (§7, Stufe VALIDATE)."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


class NotFoundError(WeltmodellError):
    """Referenziertes Objekt existiert nicht."""
