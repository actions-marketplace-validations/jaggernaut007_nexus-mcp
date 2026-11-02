"""Where Nexus-MCP and its eval keep data on disk. One place, so ``data where`` stays true."""

import os
from pathlib import Path
from typing import Tuple

MODEL_DIR_NAME = "models--BAAI--bge-small-en-v1.5"


def eval_home() -> Path:
    """Saved eval results. Set ``NEXUS_EVAL_HOME`` to move them."""
    return Path(os.environ.get("NEXUS_EVAL_HOME") or Path.home() / ".nexus-mcp" / "evals")


def eval_cache() -> Path:
    """Clones of repo URLs given to ``eval``. Set ``NEXUS_EVAL_CACHE`` to move them."""
    return Path(os.environ.get("NEXUS_EVAL_CACHE") or Path.home() / ".cache" / "nexus-mcp" / "eval")


def hf_hub_dir() -> Path:
    """Hugging Face hub cache, where the embedding model is stored."""
    home = os.environ.get("HF_HOME")
    base = Path(home) if home else Path.home() / ".cache" / "huggingface"
    return base / "hub"


def model_dir() -> Path:
    return hf_hub_dir() / MODEL_DIR_NAME


def project_store(project: Path) -> Path:
    """Index folder of a project: ``NEXUS_STORAGE_DIR`` or ``<project>/.nexus``."""
    env = os.environ.get("NEXUS_STORAGE_DIR")
    return Path(env) if env else Path(project) / ".nexus"


def dir_size(path: Path) -> Tuple[int, int]:
    """(bytes, file count) under ``path``. (0, 0) when it does not exist."""
    path = Path(path)
    if path.is_file():
        return path.stat().st_size, 1
    total = files = 0
    if path.is_dir():
        for item in path.rglob("*"):
            try:
                if item.is_file() and not item.is_symlink():
                    total += item.stat().st_size
                    files += 1
            except OSError:
                continue
    return total, files


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
