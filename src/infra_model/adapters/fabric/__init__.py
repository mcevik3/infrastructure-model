"""Offline FABRIC rendering API. No provider SDK or live operations."""

from ...model import InfrastructureModel
from .config import FabricAdapterConfig
from .errors import FabricTranslationError
from .renderer import _render
from .translator import _translate

__all__ = ["FabricAdapterConfig", "FabricTranslationError", "render_fabric"]


def render_fabric(model: InfrastructureModel, *, config: FabricAdapterConfig, cluster: str | None = None) -> dict:
    """Render one Cluster faithfully, or raise FabricTranslationError.

    Core validation runs before translation, including for models not yet queried.
    The returned dictionary has no references to the input model or configuration.
    """
    return _render(_translate(model, cluster=cluster, config=config))
