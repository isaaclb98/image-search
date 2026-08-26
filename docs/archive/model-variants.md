# SigLIP2 Model Variants

## Overview

The image-search app now supports selecting different SigLIP2 model variants via the `SIGLIP_VARIANT` environment variable. This allows you to balance between model size, speed, and quality based on your needs.

## Available Variants

| Variant | Model Name | Dimension | Use Case |
|---------|------------|-----------|----------|
| `B/16-256` | ViT-B-16-SigLIP2-256 | 768 | **Default**. Fastest, smallest (~86MB). Good for most use cases. |
| `L/16-256` | ViT-L-16-SigLIP2-256 | 1024 | Balanced. Better quality than B/16, still reasonable speed. |
| `gopt/16-384` | ViT-gopt-16-SigLIP2-384 | 1536 | Best quality, slowest (~340MB). For maximum accuracy. |

## Configuration

Set the variant in your `.env` file or as an environment variable:

```bash
SIGLIP_VARIANT=B/16-256  # Default
# or
SIGLIP_VARIANT=L/16-256
# or
SIGLIP_VARIANT=gopt/16-384
```

If not set, the app defaults to `B/16-256`.

## Important: Variant Must Match Indexed Data

**The variant is stored in `data/siglip_variant.json`** after the first run. If you change the `SIGLIP_VARIANT` environment variable, the app will detect the mismatch and refuse to start with an error like:

```
Model variant mismatch!
  Stored: B/16-256 (ViT-B-16-SigLIP2-256, 768 dim)
  Requested: gopt/16-384 (ViT-gopt-16-SigLIP2-384, 1536 dim)

You have two options:
1. Delete data/siglip_variant.json and reindex all images
2. Use the original variant: SIGLIP_VARIANT=B/16-256
```

### Why This Matters

Different model variants produce embeddings with different dimensions (768, 1024, or 1536). The Qdrant collection is created with a specific dimension, and all stored embeddings must match. Using a different variant would produce incompatible embeddings.

## Switching Variants

If you want to switch to a different variant, you need to:

1. **Stop the search service**
2. **Delete the variant file**: `rm data/siglip_variant.json`
3. **Delete the Qdrant collection** (or create a new one with a different name)
4. **Update `.env`**: Set the new `SIGLIP_VARIANT`
5. **Reindex all images**: Run the indexer with the new variant
6. **Restart the search service**

Example:
```bash
# Switch from B/16-256 to gopt/16-384
docker-compose down
rm data/siglip_variant.json
# In .env, set: SIGLIP_VARIANT=gopt/16-384
# Delete or rename the Qdrant collection
python -m indexer.index --data-dir /path/to/images
docker-compose up -d
```

## Technical Details

- **Dimension mapping**: Defined in `search/config.py` as `SIGLIP_VARIANT_DIM`
- **Storage**: Variant stored in `data/siglip_variant.json` as `{"variant": "B/16-256"}`
- **Validation**: Performed in `config.load()` and `get_encoder()` before model initialization
- **Model loading**: `text_encoder.py` uses the variant to determine which model to load
- **Qdrant**: Collection dimension is set when creating the collection (in indexer)

## Migration Path (Future)

A future enhancement could add a migration script that:
1. Reads all existing embeddings from Qdrant
2. Re-embeds images with the new model variant
3. Updates the collection schema
4. Updates `data/siglip_variant.json`

This would avoid full reindexing, but would still require processing all images.
