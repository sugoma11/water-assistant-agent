"""Argilla-specific settings loaded from environment variables."""

import argilla as rg
import pydantic_settings


class ArgillaSettings(pydantic_settings.BaseSettings):
    """Connection settings for the Argilla labeling server."""

    argilla_api_url: str = "http://localhost:6900"
    argilla_api_key: str = "argilla.apikey"
    argilla_dataset_name: str = "water_qa"
    argilla_workspace: str = "default"

    def make_client(self) -> rg.Argilla:
        """Create an Argilla client from these settings."""
        return rg.Argilla(
            api_url=self.argilla_api_url,
            api_key=self.argilla_api_key,
        )

    def with_dataset_name(self, name: str | None) -> "ArgillaSettings":
        """Return a copy with *name* as the dataset name (if not None)."""
        if name is not None:
            return self.model_copy(update={"argilla_dataset_name": name})
        return self
