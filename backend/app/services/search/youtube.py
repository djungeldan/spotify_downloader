import os
import subprocess
import json
import re
import asyncio
import urllib.parse
from typing import List, Dict, Any, Optional, Callable
from .base import SearchProvider
from .soundcloud import _run_ytdlp_with_progress

class YoutubeProvider(SearchProvider):
    @property
    def name(self) -> str:
        return "youtube"

    async def search(self, query: str, cookies_file: Optional[str] = None) -> List[Dict[str, Any]]:
        cmd = [
            "yt-dlp",
            f"ytsearch5:{query}",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            "--remote-components", "ejs:github",
            "--no-js-runtimes",
            "--js-runtimes", "node"
        ]
        if cookies_file:
            cmd.extend(["--cookies", cookies_file])

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
                        'id': f"yt_{data.get('id')}",
                        'source': self.name,
                        'filename': data.get('title'),
                        'size': 0, # Unknown until download
                        'duration': data.get('duration') or 0,
                        'extension': 'm4a/mp3', # Target
                        'bitrate': 128, # Standard YouTube
                        'download_link': data.get('url') or data.get('id'),
                        'metadata': {
                            'id': data.get('id'),
                            'uploader': data.get('uploader')
                        }
                    })
                except:
                    continue
            
            return results
        except Exception as e:
            print(f"YouTube Search Error: {e}")
            return []

    async def download(
        self,
        result: Dict[str, Any],
        output_path: str,
        progress_callback: Optional[Callable] = None
    ) -> bool:
        video_id = result['metadata']['id']
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Download best native audio with metadata and progress and convert to MP3
        cmd = [
            "yt-dlp",
            "-f", "bestaudio/best",      # Highest bitrate stream first
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "0",
            "--embed-thumbnail",          # Embed album art
            "--add-metadata",             # Embed ID3 tags
            "--newline",                  # Flush progress lines immediately
            "-o", f"{output_path}/%(title)s.%(ext)s",
            url
        ]
        
        try:
            return await _run_ytdlp_with_progress(cmd, progress_callback)
        except Exception as e:
            print(f"YouTube Download Error: {e}")
            return False
