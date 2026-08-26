"""
SoundCloud profile/playlist scraper using yt-dlp's internal Python API.

Uses fast flat extraction (`extract_flat=True`) to instantly enumerate track URLs
from a profile or playlist with a 7m 30s (450s) duration limit and strict track deduplication.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

# Max duration limit: 7 minutes (420 seconds)
MAX_DURATION_SECS = 420


def _enumerate_sc_tracks_sync(
    profile_url: str,
    log_callback: Optional[Callable[[str], None]] = None,
    sc_oauth_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Synchronously enumerate tracks from a SoundCloud profile/playlist URL
    using fast flat extraction. Skips tracks > 7m 30s and deduplicates entries.
    """
    import yt_dlp, os

    def log(msg: str):
        logger.info(f"[SC Scraper] {msg}")
        if log_callback:
            try:
                log_callback(msg)
            except Exception:
                pass

    log(f"Starting SoundCloud flat extraction for: {profile_url}")

    oauth_token = sc_oauth_token or os.getenv("SOUNDCLOUD_OAUTH_TOKEN")

    flat_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'ignoreerrors': True,
    }
    if oauth_token and oauth_token.strip():
        tok = oauth_token.strip()
        flat_opts['http_headers'] = {
            'Authorization': f'OAuth {tok}',
            'Cookie': f'oauth_token={tok}',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

    try:
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)
    except Exception as e:
        log(f"Extraction failed: {e}")
        return []

    if info is None:
        log(f"yt-dlp returned no information for {profile_url}")
        return []

    uploader = info.get('uploader') or info.get('title') or 'SoundCloud'

    # Build raw entries list
    if info.get('_type') not in ('playlist', 'multi_video'):
        raw_entries = [info]
    else:
        raw_entries = [e for e in (info.get('entries') or []) if e]

    log(f"Extracted {len(raw_entries)} raw entries from SoundCloud")

    tracks: List[Dict[str, Any]] = []
    seen_urls = set()
    skipped_duration = 0

    for i, entry in enumerate(raw_entries):
        title = entry.get('title') or f"Track {i+1}"
        track_uploader = entry.get('uploader') or uploader

        track_url = entry.get('webpage_url') or entry.get('url')
        if not track_url:
            continue
        if not track_url.startswith('http'):
            track_url = f"https://soundcloud.com{track_url}"

        # Deduplication check
        clean_url = track_url.split('?')[0].lower()
        if clean_url in seen_urls:
            continue
        seen_urls.add(clean_url)

        duration = entry.get('duration') or 0

        # Enforce 7 minutes (420 seconds) limit
        if duration > MAX_DURATION_SECS:
            mins = int(duration // 60)
            secs = int(duration % 60)
            log(f"Skipping '{title}' ({mins}:{secs:02d} > 7:00 limit)")
            skipped_duration += 1
            continue

        track_id = str(entry.get('id') or f"sc_track_{i+1}")

        tracks.append({
            'title': title,
            'uploader': track_uploader,
            'url': track_url,
            'id': track_id,
            'duration': duration,
        })

    log(f"Enumeration complete: {len(tracks)} unique tracks ready (skipped {skipped_duration} > 7m00s)")
    return tracks


async def get_profile_tracks(
    profile_url: str,
    log_callback: Optional[Callable[[str], None]] = None,
    sc_oauth_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Async wrapper around flat track enumeration."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _enumerate_sc_tracks_sync, profile_url, log_callback, sc_oauth_token
    )
