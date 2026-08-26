"""Print actual durations for all saintludo tracks."""
import yt_dlp

# Step 1: flat extraction for URL list
flat_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True, 'ignoreerrors': True}
with yt_dlp.YoutubeDL(flat_opts) as ydl:
    info = ydl.extract_info("https://soundcloud.com/saintludo", download=False)

entries = [e for e in (info.get('entries') or []) if e]
print(f"Total entries: {len(entries)}")

# Step 2: per-track metadata
meta_opts = {'quiet': True, 'no_warnings': True, 'ignoreerrors': True}
for entry in entries:
    url = entry.get('url') or entry.get('webpage_url', '')
    if not url.startswith('http'):
        url = f"https://soundcloud.com{url}"
    with yt_dlp.YoutubeDL(meta_opts) as ydl:
        meta = ydl.extract_info(url, download=False)
    if meta:
        dur = meta.get('duration') or 0
        flag = " *** OVER 7 MIN ***" if dur > 420 else ""
        print(f"  {int(dur//60)}:{int(dur%60):02d}  {meta.get('title')}{flag}")
