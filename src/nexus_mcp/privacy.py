"""Privacy defaults: no telemetry, and an offline switch for the embedding model download."""

import os

_TRUE = ("1", "true", "yes")


def apply_privacy_defaults() -> None:
    """Turn off Hugging Face telemetry, and go offline when ``NEXUS_OFFLINE`` is set.

    Nexus-MCP makes no network call of its own. The one network step is the first
    download of the embedding model. ``NEXUS_OFFLINE=1`` blocks it, so the model must
    already be in the cache. A value the user set in the environment is kept.
    """
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if os.environ.get("NEXUS_OFFLINE", "").lower() in _TRUE:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
