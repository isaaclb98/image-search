from __future__ import annotations

from search import config


def test_default_result_limit_is_20(monkeypatch, tmp_path):
    monkeypatch.delenv("TOP_K_DEFAULT", raising=False)
    monkeypatch.setenv("SEARCH_TEST_MODE", "1")
    monkeypatch.setenv("NAS_IMAGES_BASE", str(tmp_path))
    monkeypatch.setenv("MODEL_NAME", "ViT-gopt-16-SigLIP2-384")

    loaded = config.load()

    assert loaded.top_k_default == 20
