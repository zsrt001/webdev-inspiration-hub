"""
ULTIMATE RESTORATION - 12 HIGH-END WEDDING SAMPLES.
Version 5: Final survival IDs for stubborn 404s.
"""
import os
import httpx
import time

SAVE_DIR = os.path.join(os.path.dirname(__file__), "static", "styles")
os.makedirs(SAVE_DIR, exist_ok=True)

SAMPLES = {
    "chn_xiuhe.jpg": "photo-1583939003579-730e3918a45a", 
    "hk_retro.jpg": "photo-1512453979798-5ea266f8880c", 
    "classic_bw.jpg": "photo-1532712938310-34cb3982ef74", 
    "kor_minimal.jpg": "photo-1537633552985-df8429e8048b", 
    "royal_castle.jpg": "photo-1519741497674-611481863552", 
    "old_money.jpg": "photo-1509927083803-4bd519298ac4", 
    "gothic_romance.jpg": "photo-1509248961158-e54f6934749c",
    "twilight_forest.jpg": "photo-1469334031218-e382a71b716b", 
    "beach_sunset.jpg": "photo-1515934751635-c81c6bc9a2d8", 
    "jp_shiromuku.jpg": "photo-1542332213-9b5a5a3fab35", 
    "cyber_city.jpg": "photo-1605810230434-7631ac76ec81", 
    "school_days.jpg": "photo-1522202176988-66273c2fd55f",
}

def download():
    headers = {"User-Agent": "Mozilla/5.0"}
    client = httpx.Client(follow_redirects=True, timeout=60, headers=headers)
    for n, pid in SAMPLES.items():
        if os.path.exists(os.path.join(SAVE_DIR, n)) and os.path.getsize(os.path.join(SAVE_DIR, n)) > 1000:
            continue # Already got it
        print(f"🎬 Processing: {n}...")
        url = f"https://images.unsplash.com/{pid}?q=90&w=1500&auto=format&fit=crop"
        try:
            response = client.get(url)
            if response.status_code == 200:
                with open(os.path.join(SAVE_DIR, n), 'wb') as f:
                    f.write(response.content)
                print(f"   ✨ Saved: {len(response.content)//1024} KB")
            else:
                print(f"   ❌ HTTP Error {response.status_code}")
        except Exception as e:
            print(f"   ❌ Exception: {e}")
    client.close()

if __name__ == "__main__":
    download()
