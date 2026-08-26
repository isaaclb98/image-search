"""
Test model variant configuration and validation.
"""
import os
import pytest
from search import config
from indexer import upsert


def test_default_variant_is_l16():
    """Default variant should be L/16-256 (1024-dim)."""
    # Clear env to test default
    os.environ.pop("SIGLIP_VARIANT", None)
    os.environ.pop("MODEL_NAME", None)
    
    variant = config.get_siglip_variant()
    assert variant == "L/16-256"
    
    model_name = config.get_model_name_for_variant(variant)
    assert model_name == "ViT-L-16-SigLIP2-256"
    
    dim = config.get_vector_dim_for_variant(variant)
    assert dim == 1024


def test_gopt_variant():
    """gopt variant should be 1536-dim."""
    os.environ["SIGLIP_VARIANT"] = "gopt/16-384"
    
    variant = config.get_siglip_variant()
    assert variant == "gopt/16-384"
    
    model_name = config.get_model_name_for_variant(variant)
    assert model_name == "ViT-gopt-16-SigLIP2-384"
    
    dim = config.get_vector_dim_for_variant(variant)
    assert dim == 1536


def test_invalid_variant_raises():
    """Invalid variant should raise ValueError."""
    os.environ["SIGLIP_VARIANT"] = "invalid-variant"
    
    with pytest.raises(ValueError, match="Invalid SIGLIP_VARIANT"):
        config.get_siglip_variant()


def test_variant_validation():
    """validate_variant_against_collection should detect mismatches."""
    # This test would need a mock Qdrant client
    # For now, just verify the function exists
    assert hasattr(config, 'validate_variant_against_collection')
