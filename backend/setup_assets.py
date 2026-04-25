"""
Asset Setup Script - Download and save style thumbnails locally.
Run this script once to populate the static/styles directory.

Usage: python setup_assets.py
"""
import os
import httpx

# Directory to save assets
SAVE_DIR = os.path.join(os.path.dirname(__file__), "static", "styles")
os.makedirs(SAVE_DIR, exist_ok=True)

# Source URL -> Target Filename mapping
# These are high-stability, style-accurate source URLs
ASSETS = {
    "xiuhe.jpg": "https://images.unsplash.com/photo-1519741497674-611481863552?q=80&w=600&fit=crop",
    "hk_retro.jpg": "https://images.unsplash.com/photo-1544078751-58fee2d8a03b?q=80&w=600&fit=crop",
    "vintage_bw.jpg": "https://images.unsplash.com/photo-1537633552985-df8429e8048b?q=80&w=600&fit=crop",
    "kor_white.jpg": "https://images.unsplash.com/photo-1591604466107-ec97de577aff?q=80&w=600&fit=crop",
    "kor_canvas.jpg": "https://images.unsplash.com/photo-1519225421980-715cb0215aed?q=80&w=600&fit=crop",
    "west_classic.jpg": "https://images.unsplash.com/photo-1520854221256-17451cc330e7?q=80&w=600&fit=crop",
    "west_castle.jpg": "https://images.unsplash.com/photo-1465495976277-4387d4b0b4c6?q=80&w=600&fit=crop",
    "west_manor.jpg": "https://images.unsplash.com/photo-1583939003579-730e3918a45a?q=80&w=600&fit=crop",
    "out_forest.jpg": "https://images.unsplash.com/photo-1515934751635-c81c6bc9a2d8?q=80&w=600&fit=crop",
    "out_beach.jpg": "https://images.unsplash.com/photo-1532712938310-34cb3982ef74?q=80&w=600&fit=crop",
    "out_sunset.jpg": "https://images.unsplash.com/photo-1606216794074-735e91aa2c92?q=80&w=600&fit=crop",
    "custom_placeholder.jpg": "https://placehold.co/600x900/333/fff.jpg?text=Upload+Scene"
}

def download_assets():
    """Download all assets to local static directory."""
    print(f"📁 Saving assets to: {SAVE_DIR}")
    print(f"📦 Total assets: {len(ASSETS)}")
    print("-" * 50)
    
    success_count = 0
    for filename, url in ASSETS.items():
        filepath = os.path.join(SAVE_DIR, filename)
        
        if os.path.exists(filepath):
            print(f"⏭️  Skipping {filename} (already exists)")
            success_count += 1
            continue
            
        try:
            print(f"⬇️  Downloading {filename}...")
            response = httpx.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }, follow_redirects=True)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            print(f"✅ Saved {filename} ({len(response.content) / 1024:.1f} KB)")
            success_count += 1
            
        except Exception as e:
            print(f"❌ Failed to download {filename}: {e}")
    
    print("-" * 50)
    print(f"✨ Asset setup complete: {success_count}/{len(ASSETS)} files ready")
    return success_count == len(ASSETS)


if __name__ == "__main__":
    download_assets()
