import os
import subprocess
import json
import re
import asyncio
from typing import List, Dict, Any, Optional, Callable
from .base import SearchProvider

# Matches lines like: [download]  42.3% of 5.20MiB at 1.23MiB/s ETA 00:03
_PROGRESS_RE = re.compile(
    r'\[download\]\s+([\d.]+)%\s+of\s+([\d.]+\S+)\s+at\s+([\S]+)\s+ETA\s+(\S+)'
)


async def _run_ytdlp_with_progress(
    cmd: list,
    progress_callback: Optional[Callable] = None
) -> bool:
    """Run a yt-dlp command, continuously draining stdout to avoid pipe deadlock."""
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )

    while True:
        line_bytes = await process.stdout.readline()
        if not line_bytes:
            break
        if progress_callback:
            line = line_bytes.decode('utf-8', errors='ignore').rstrip()
            m = _PROGRESS_RE.search(line)
            if m:
                try:
                    res = progress_callback({
                        "progress": float(m.group(1)),
                        "speed": m.group(3),
                        "state": "downloading",
                        "eta": m.group(4),
                    })
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

    await process.wait()
    return process.returncode == 0


class SoundCloudProvider(SearchProvider):
    @property
    def name(self) -> str:
        return "soundcloud"

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """
        Search SoundCloud using yt-dlp.
        yt-dlp natively supports SoundCloud.
        """
        
        cmd = [
            "yt-dlp",
            f"scsearch5:{query}",  # SoundCloud search
            "--dump-json",
            "--flat-playlist",
            "--no-warnings"
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = stdout_bytes.decode('utf-8')
            
            results = []
            for line in stdout.splitlines():
                try:
                    data = json.loads(line)
                    results.append({
                        'id': f"sc_{data.get('id')}",
                        'source': self.name,
                        'filename': data.get('title'),
                        'size': 0,  # Unknown until download
                        'duration': data.get('duration') or 0,
                        'extension': 'm4a/mp3',  # SoundCloud quality varies
                        'bitrate': 128,  # Approximate
                        'download_link': data.get('url') or data.get('id'),
                        'metadata': {
                            'id': data.get('id'),
                            'uploader': data.get('uploader'),
                            'url': data.get('webpage_url', data.get('url'))
                        }
                    })
                except:
                    continue
            
            return results
        except Exception as e:
            print(f"SoundCloud Search Error: {e}")
            return []

    async def download(
        self,
        result: Dict[str, Any],
        output_path: str,
        progress_callback: Optional[Callable] = None,
        sc_oauth_token: Optional[str] = None
    ) -> bool:
        """
        Download from SoundCloud using yt-dlp with optional real-time progress.
        """
        url = result['metadata'].get('url') or result['download_link']
        oauth_token = sc_oauth_token or os.getenv("SOUNDCLOUD_OAUTH_TOKEN")
        
        cmd = [
            "yt-dlp",
            "-f", "bestaudio/best",      # Highest bitrate stream first
            "--no-playlist",             # Force single track — don't expand sets/playlists
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",          # Embed album art
            "--add-metadata",             # Embed original ID3 tags
            "--user-agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "--newline",                  # Flush progress lines immediately
            "-o", f"{output_path}/%(title)s.%(ext)s",
        ]

        if oauth_token and oauth_token.strip():
            tok = oauth_token.strip()
            cmd.extend([
                "--add-header", f"Authorization: OAuth {tok}",
                "--add-header", f"Cookie: oauth_token={tok}",
            ])

        cmd.append(url)
        
        try:
            return await _run_ytdlp_with_progress(cmd, progress_callback)
        except Exception as e:
            print(f"SoundCloud Download Error: {e}")
            return False

    async def download_by_search(
        self,
        artist: str,
        title: str,
        output_path: str,
        progress_callback: Optional[Callable] = None,
        sc_oauth_token: Optional[str] = None
    ) -> bool:
        """
        Fallback direct SoundCloud stream download using internal scsearch1 query.
        Guarantees direct audio stream download from SoundCloud API even if track webpage returns 404.
        """
        art = (artist or "").strip()
        tit = (title or "").strip()
        if art and tit.lower().startswith(art.lower()):
            search_str = tit
        elif art:
            search_str = f"{art} {tit}"
        else:
            search_str = tit

        query = f"scsearch1:{search_str}"
        oauth_token = sc_oauth_token or os.getenv("SOUNDCLOUD_OAUTH_TOKEN")

        cmd = [
            "yt-dlp",
            "-f", "bestaudio/best",
            "--no-playlist",
            "--extract-audio",
            "--embed-thumbnail",
            "--add-metadata",
            "--user-agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
            "--newline",
            "-o", f"{output_path}/%(title)s.%(ext)s",
        ]

        if oauth_token and oauth_token.strip():
            tok = oauth_token.strip()
            cmd.extend([
                "--add-header", f"Authorization: OAuth {tok}",
                "--add-header", f"Cookie: oauth_token={tok}",
            ])

        cmd.append(query)

        try:
            return await _run_ytdlp_with_progress(cmd, progress_callback)
        except Exception as e:
            print(f"SoundCloud Search Download Error: {e}")
            return False
