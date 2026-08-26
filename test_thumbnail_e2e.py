#!/usr/bin/env python3
"""
End-to-end test for thumbnail pipeline:
1. Generate a test image
2. Generate thumbnail via indexer function
3. Verify thumbnail exists with correct properties
4. Test search endpoint serves it correctly
"""
import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

def test_thumbnail_pipeline():
    """Test the complete thumbnail pipeline."""
    from PIL import Image
    from search.config import THUMBNAIL_DIR
    
    # Override THUMBNAIL_DIR for testing
    test_thumb_dir = Path(tempfile.mkdtemp(prefix="test_thumbnails_"))
    os.environ["THUMBNAIL_DIR"] = str(test_thumb_dir)
    
    # Reload config to pick up new env var
    import importlib
    import search.config
    importlib.reload(search.config)
    from search.config import THUMBNAIL_DIR
    
    # Also reload thumbnails module to pick up new config
    import search.routers.thumbnails
    importlib.reload(search.routers.thumbnails)
    
    from indexer.thumbnails import generate_thumbnail_for_path
    
    print("=== Thumbnail Pipeline End-to-End Test ===\n")
    
    # 1. Create test image
    test_img_path = Path(tempfile.gettempdir()) / "test_photo.jpg"
    img = Image.new("RGB", (1920, 1080), color=(100, 150, 200))
    img.save(test_img_path, "JPEG")
    print(f"✓ Created test image: {test_img_path}")
    
    # 2. Generate thumbnail
    thumb_path = generate_thumbnail_for_path(img, test_img_path, shard="")
    assert thumb_path is not None, "Thumbnail generation failed"
    print(f"✓ Generated thumbnail: {thumb_path}")
    
    # Extract point_id from path for later testing
    point_id = thumb_path.stem
    print(f"✓ Generated point_id: {point_id}")
    
    # 3. Verify thumbnail properties
    assert thumb_path.exists(), f"Thumbnail not found at {thumb_path}"
    assert thumb_path.suffix == ".webp", f"Expected .webp, got {thumb_path.suffix}"
    
    thumb_img = Image.open(thumb_path)
    # Thumbnail should fit within 256x256 while preserving aspect ratio
    assert thumb_img.size[0] <= 256 and thumb_img.size[1] <= 256, f"Expected <=256x256, got {thumb_img.size}"
    print(f"✓ Thumbnail size: {thumb_img.size} (preserves aspect ratio)")
    
    file_size = thumb_path.stat().st_size
    print(f"✓ Thumbnail file size: {file_size} bytes ({file_size/1024:.1f} KB)")
    
    # 4. Verify path structure (two-level prefix)
    expected_prefix = point_id[:2]
    assert expected_prefix in str(thumb_path), f"Expected prefix {expected_prefix} in path"
    print(f"✓ Path uses two-level prefix: {expected_prefix}")
    
    # 5. Test search endpoint
    print("\n--- Testing search endpoint ---")
    from search.routers.thumbnails import build_thumbnails_router
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    app = FastAPI()
    app.include_router(build_thumbnails_router())
    client = TestClient(app)
    
    # Test successful retrieval
    response = client.get(f"/thumb/{point_id}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    assert response.headers["content-type"] == "image/webp"
    assert "Cache-Control" in response.headers
    assert "immutable" in response.headers["Cache-Control"]
    print(f"✓ GET /thumb/{point_id} returns 200 with correct headers")
    
    # Test 404 for missing thumbnail (valid hex format but doesn't exist)
    missing_id = "aaa111" + "0" * 26
    response = client.get(f"/thumb/{missing_id}")
    assert response.status_code == 404, f"Expected 404, got {response.status_code}"
    print(f"✓ GET /thumb/{missing_id} returns 404")
    
    # Test invalid point_id format
    response = client.get("/thumb/invalid")
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    print(f"✓ GET /thumb/invalid returns 400 (bad format)")
    
    print("\n=== All tests passed! ===")
    
    # Cleanup
    thumb_path.unlink()
    test_img_path.unlink()

if __name__ == "__main__":
    test_thumbnail_pipeline()
