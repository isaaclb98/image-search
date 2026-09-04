"""
Test model variant configuration and validation.

Uses monkeypatch for all env var mutations so each test's changes
are restored automatically — prevents the SIGLIP_VARIANT='invalid'
from leaking into subsequent test modules (test_v2_smoke.py
chokes on it because config.get_siglip_variant() reads the env
at call time, not at import time).
"""
import json
import pytest
from search import config


# ----- Variant lookup -----

def test_default_variant_is_so400m(monkeypatch):
    """Default variant should be so400m/16-384 (1152-dim) when env unset.

    As of the model-variant migration plan, so400m is the prod default;
    L/16-256 is no longer the default — it's still registered and
    selectable via SIGLIP_VARIANT=L/16-256, just not the default.
    """
    monkeypatch.delenv("SIGLIP_VARIANT", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)

    variant = config.get_siglip_variant()
    assert variant == "so400m/16-384"

    model_name = config.get_model_name_for_variant(variant)
    assert model_name == "ViT-so400m-patch16-384"

    dim = config.get_vector_dim_for_variant(variant)
    assert dim == 1152


@pytest.mark.parametrize("variant,expected_model,expected_dim", [
    ("B/16-256", "ViT-B-16-SigLIP2-256", 768),
    ("L/16-256", "ViT-L-16-SigLIP2-256", 1024),
    ("gopt/16-384", "ViT-gopt-16-SigLIP2-384", 1536),
    ("so400m/16-384", "ViT-so400m-patch16-384", 1152),
])
def test_all_known_variants(monkeypatch, variant, expected_model, expected_dim):
    """Each registered variant maps to the right model and dim."""
    monkeypatch.setenv("SIGLIP_VARIANT", variant)
    assert config.get_siglip_variant() == variant
    assert config.get_model_name_for_variant(variant) == expected_model
    assert config.get_vector_dim_for_variant(variant) == expected_dim
    assert config.get_vector_dim() == expected_dim


def test_gopt_variant(monkeypatch):
    """gopt variant should be 1536-dim."""
    monkeypatch.setenv("SIGLIP_VARIANT", "gopt/16-384")

    variant = config.get_siglip_variant()
    assert variant == "gopt/16-384"

    model_name = config.get_model_name_for_variant(variant)
    assert model_name == "ViT-gopt-16-SigLIP2-384"

    dim = config.get_vector_dim_for_variant(variant)
    assert dim == 1536


def test_invalid_variant_raises(monkeypatch):
    """Invalid variant should raise ValueError with helpful message."""
    monkeypatch.setenv("SIGLIP_VARIANT", "invalid-variant")

    with pytest.raises(ValueError) as exc_info:
        config.get_siglip_variant()
    assert "Invalid SIGLIP_VARIANT" in str(exc_info.value)
    assert "B/16-256" in str(exc_info.value)
    assert "L/16-256" in str(exc_info.value)
    assert "gopt/16-384" in str(exc_info.value)


@pytest.mark.parametrize("bad_variant", [
    "",
    "b/16-256",                          # case-sensitive
    "L/16-512",                          # valid format but no such variant
    "gopt-16-384",                       # wrong separator
    "ViT-L-16-SigLIP2-256",              # model name, not variant name
    "random",
    "L/16",                              # missing resolution
])
def test_various_invalid_variants_raise(monkeypatch, bad_variant):
    """Various malformed variant names all raise ValueError."""
    monkeypatch.setenv("SIGLIP_VARIANT", bad_variant)
    with pytest.raises(ValueError, match="Invalid SIGLIP_VARIANT"):
        config.get_siglip_variant()


def test_unknown_variant_in_lookup_functions(monkeypatch):
    """get_model_name_for_variant / get_vector_dim_for_variant
    reject unknown variants even when the global env is valid."""
    monkeypatch.setenv("SIGLIP_VARIANT", "L/16-256")
    with pytest.raises(ValueError, match="Unknown variant"):
        config.get_model_name_for_variant("not-a-real-variant")
    with pytest.raises(ValueError, match="Unknown variant"):
        config.get_vector_dim_for_variant("not-a-real-variant")


def test_get_vector_dim_uses_active_variant(monkeypatch):
    """get_vector_dim() reads from the active env-configured variant."""
    monkeypatch.setenv("SIGLIP_VARIANT", "B/16-256")
    assert config.get_vector_dim() == 768

    monkeypatch.setenv("SIGLIP_VARIANT", "gopt/16-384")
    assert config.get_vector_dim() == 1536


def test_siglip_variants_dict_is_complete():
    """SIGLIP_VARIANTS dict should have all four documented variants
    with valid model names and positive dims."""
    assert set(config.SIGLIP_VARIANTS.keys()) == {
        "B/16-256", "L/16-256", "gopt/16-384", "so400m/16-384",
    }
    for variant, (model_name, dim) in config.SIGLIP_VARIANTS.items():
        assert isinstance(model_name, str)
        assert model_name.startswith("ViT-"), f"{variant}: bad model name {model_name!r}"
        # The so400m HF repo is named `timm/ViT-so400m-patch16-384`
        # without a "SigLIP2" suffix (quirk of HF repo naming); the
        # other three have "SigLIP2" in their HF repo names. Accept
        # either, since `_CENTROID_MODEL_COMPAT` is what actually
        # drives the model-family grouping.
        assert ("SigLIP2" in model_name or "so400m" in model_name), (
            f"{variant}: model {model_name!r} is not in the SigLIP2 family"
        )
        assert isinstance(dim, int)
        assert dim > 0
        assert dim in (768, 1024, 1152, 1536), f"{variant}: unexpected dim {dim}"


# ----- Variant persistence (data/siglip_variant.json) -----

class TestVariantPersistence:
    """Tests for load_stored_variant / save_variant / validate_variant_against_stored."""

    def test_load_stored_returns_none_when_file_missing(self, tmp_path):
        """No config file → load returns None (first-run behavior)."""
        assert config.load_stored_variant(str(tmp_path)) is None

    def test_save_then_load_roundtrip(self, tmp_path):
        """save_variant creates the file; load_stored_variant reads it back."""
        config.save_variant("gopt/16-384", str(tmp_path))
        assert config.load_stored_variant(str(tmp_path)) == "gopt/16-384"

    def test_save_creates_parent_dir(self, tmp_path):
        """save_variant should mkdir -p the data dir."""
        nested = tmp_path / "a" / "b" / "c"
        config.save_variant("L/16-256", str(nested))
        assert (nested / config.VARIANT_CONFIG_FILE).exists()

    def test_save_overwrites_existing(self, tmp_path):
        """save_variant overwrites an existing variant file."""
        config.save_variant("B/16-256", str(tmp_path))
        config.save_variant("gopt/16-384", str(tmp_path))
        assert config.load_stored_variant(str(tmp_path)) == "gopt/16-384"

    def test_save_then_load_returns_exact_variant(self, tmp_path):
        """Variant roundtrips exactly (no whitespace, no normalization)."""
        for v in ["B/16-256", "L/16-256", "gopt/16-384"]:
            config.save_variant(v, str(tmp_path))
            assert config.load_stored_variant(str(tmp_path)) == v

    def test_load_handles_corrupt_json(self, tmp_path):
        """Corrupt JSON in the variant file should not crash; return None."""
        path = config.get_variant_config_path(str(tmp_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")
        assert config.load_stored_variant(str(tmp_path)) is None

    def test_load_handles_empty_file(self, tmp_path):
        """Empty file should not crash; return None or {}→None."""
        path = config.get_variant_config_path(str(tmp_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        assert config.load_stored_variant(str(tmp_path)) is None

    def test_load_handles_missing_variant_key(self, tmp_path):
        """Valid JSON without 'variant' key returns None."""
        path = config.get_variant_config_path(str(tmp_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"other_key": "value"}))
        assert config.load_stored_variant(str(tmp_path)) is None

    def test_get_variant_config_path_under_data_dir(self, tmp_path):
        """Path should be data_dir / VARIANT_CONFIG_FILE."""
        path = config.get_variant_config_path(str(tmp_path))
        assert path == tmp_path / config.VARIANT_CONFIG_FILE


class TestValidateVariantAgainstStored:
    """Tests for validate_variant_against_stored — the runtime guard."""

    def test_first_run_saves_variant(self, tmp_path):
        """No stored variant → save the env variant, no error."""
        config.validate_variant_against_stored("L/16-256", str(tmp_path))
        assert config.load_stored_variant(str(tmp_path)) == "L/16-256"

    def test_matching_variant_passes(self, tmp_path):
        """Matching stored variant → no error."""
        config.save_variant("L/16-256", str(tmp_path))
        config.validate_variant_against_stored("L/16-256", str(tmp_path))  # should not raise

    def test_mismatched_variant_raises(self, tmp_path):
        """Different stored variant → raises ValueError with both names."""
        config.save_variant("L/16-256", str(tmp_path))
        with pytest.raises(ValueError) as exc_info:
            config.validate_variant_against_stored("gopt/16-384", str(tmp_path))
        msg = str(exc_info.value)
        assert "L/16-256" in msg
        assert "gopt/16-384" in msg

    def test_mismatch_error_includes_dim_warning(self, tmp_path):
        """Mismatch error should mention re-indexing (dim differ)."""
        config.save_variant("L/16-256", str(tmp_path))
        with pytest.raises(ValueError) as exc_info:
            config.validate_variant_against_stored("B/16-256", str(tmp_path))
        msg = str(exc_info.value).lower()
        # Should warn about needing to re-index since dims differ (1024 vs 768)
        assert "re-index" in msg or "reindex" in msg or "index" in msg

    def test_all_three_variants_roundtrip(self, tmp_path):
        """Each of the 3 variants can be saved and validated against itself."""
        for v in ["B/16-256", "L/16-256", "gopt/16-384"]:
            # Clear any prior stored variant so each iteration starts fresh
            cfg_path = config.get_variant_config_path(str(tmp_path))
            if cfg_path.exists():
                cfg_path.unlink()
            config.validate_variant_against_stored(v, str(tmp_path))
            assert config.load_stored_variant(str(tmp_path)) == v
            # And validating again with the same variant passes
            config.validate_variant_against_stored(v, str(tmp_path))


# ----- DEFAULT_MODEL constant -----

def test_default_model_constant_resolves():
    """DEFAULT_MODEL should be the model name for the default variant."""
    assert config.get_model_name_for_variant(config.DEFAULT_VARIANT) == config.DEFAULT_MODEL
