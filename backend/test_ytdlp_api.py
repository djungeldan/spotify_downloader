"""
Test yt-dlp Python API to enumerate SoundCloud user tracks.
"""
import sys
sys.path.insert(0, '/app')

profile_url = "https://soundcloud.com/explorersoftheinternet"

print(f"Testing yt-dlp Python API on: {profile_url}")
try:
    import yt_dlp

    ydl_opts = {
        'quiet': False,
        'extract_flat': True,       # Don't download, just enumerate
        'ignoreerrors': True,
        'playlistend': 5,           # Just check first 5 for the test
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(profile_url, download=False)
        if info is None:
            print("FAILED: extract_info returned None")
        else:
            kind = info.get('_type', 'unknown')
            print(f"Type: {kind}")
            entries = info.get('entries', [])
            print(f"Entries: {len(list(entries))}")
            # Re-extract since list() consumed generator
            info2 = ydl.extract_info(profile_url, download=False)
            for i, entry in enumerate(info2.get('entries', [])):
                if entry:
                    print(f"  [{i}] {entry.get('title')} - {entry.get('url') or entry.get('webpage_url')}")
                if i >= 4:
                    break

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
