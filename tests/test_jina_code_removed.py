"""jina-code, `trust_remote_code` and the PyTorch dependency were removed in 3.0.0 (ADR-029).

These tests replace `test_model_deprecation.py` and `test_trust_remote_code.py`. The
requirement changed: the model that was deprecated in 2.x is now gone, and a person who
still names it gets a message that says what to do.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_mcp.config import Settings, reset_settings
from nexus_mcp.core.exceptions import ConfigurationError
from nexus_mcp.indexing import embedding_service as es

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean():
    reset_settings()
    es.reset_embedding_service()
    yield
    reset_settings()
    es.reset_embedding_service()


def test_registry_has_no_jina_code_and_no_trust_remote_code_key():
    assert "jina-code" not in es.EMBEDDING_MODELS
    assert not any("trust_remote_code" in config for config in es.EMBEDDING_MODELS.values())


def test_settings_have_no_trust_remote_code_and_ignore_the_old_variable(monkeypatch):
    monkeypatch.setenv("NEXUS_TRUST_REMOTE_CODE", "true")
    settings = Settings()  # an old setup that still sets the variable must not break
    assert not hasattr(settings, "trust_remote_code")


def test_naming_jina_code_in_the_settings_gives_the_removal_message(monkeypatch):
    monkeypatch.setenv("NEXUS_EMBEDDING_MODEL", "jina-code")
    reset_settings()
    with pytest.raises(ConfigurationError, match="removed in 3.0.0"):
        es.get_embedding_service(Settings().embedding_model)


def test_hosted_manifests_do_not_offer_jina_code():
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text())
    assert "jina-code" not in json.dumps(manifest)
    assert "jina-code" not in (ROOT / "docker" / "Dockerfile.stdio").read_text()
    assert "jina-code" not in (ROOT / "glama.json").read_text()


def test_the_eval_candidates_do_not_include_jina_code():
    from evals.retrieval import candidates

    assert "jina-code" not in candidates.CANDIDATES


def _pyproject():
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_core_dependencies_do_not_include_pytorch():
    names = [d.split(">")[0].split("=")[0].split("[")[0].strip().lower()
             for d in _pyproject()["dependencies"]]
    for banned in ("torch", "sentence-transformers", "optimum", "transformers"):
        assert banned not in names
    for needed in ("onnxruntime", "tokenizers", "huggingface-hub", "numpy"):
        assert needed in names


def test_pytorch_is_an_optional_extra_and_the_gpu_extras_are_gone():
    extras = _pyproject()["optional-dependencies"]
    assert any(d.startswith("sentence-transformers") for d in extras["torch"])
    assert "gpu" not in extras
    assert "onnx" not in extras


def test_importing_the_server_does_not_import_pytorch():
    """The default install has no torch. Even when torch is installed, the default model
    and the server must not import it (it costs 1 s and 300 MB)."""
    code = (
        "import sys\n"
        "import nexus_mcp.server, nexus_mcp.indexing.pipeline\n"
        "from nexus_mcp.indexing.embedding_service import EmbeddingService\n"
        "EmbeddingService()\n"
        "banned = ('torch', 'sentence_transformers', 'optimum', 'transformers')\n"
        "bad = [m for m in banned if m in sys.modules]\n"
        "sys.exit('imported: ' + ', '.join(bad) if bad else 0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-500:]
