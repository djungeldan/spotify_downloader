import asyncio
import sys
sys.path.insert(0, '/app')

from app.services.soundcloud_scraper import _get_sc_client_id, get_profile_track_urls

async def main():
    print("Step 1: Getting client_id...")
    client_id = await _get_sc_client_id()
    print(f"client_id = {client_id}")

    if not client_id:
        print("FAILED: no client_id")
        return

    print("\nStep 2: Getting track URLs...")
    urls = await get_profile_track_urls("https://soundcloud.com/explorersoftheinternet")
    print(f"Found {len(urls)} URLs")
    for u in urls[:5]:
        print(f"  {u}")

asyncio.run(main())
