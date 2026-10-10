"""Structured offline translation diagnostics."""

from ...errors import InfrastructureError


class FabricTranslationError(InfrastructureError):
    """Intent or configuration cannot be faithfully rendered for FABRIC."""

    def __init__(self, reason: str, *, entity: str = "", path: str = "", source: str = ""):
        self.entity = entity
        self.path = path
        self.source = source
        self.reason = reason
        location = f"{source}{path}"
        context = ": ".join(part for part in (location, entity) if part)
        super().__init__(f"{context + ': ' if context else ''}FABRIC translation: {reason}")
