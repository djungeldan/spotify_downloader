import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

from .search.soundcloud import SoundCloudProvider, _run_ytdlp_with_progress
from .search.youtube import YoutubeProvider
from .spotify import SpotifyService
from .soundcloud_scraper import get_profile_tracks

logger = logging.getLogger(__name__)

# Session timeout (30 minutes)
SESSION_TIMEOUT_SECS = 1800


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        if client_id not in self.active_connections:
            self.active_connections[client_id] = []
        self.active_connections[client_id].append(websocket)

    def disconnect(self, websocket: WebSocket):
        for client_id, conns in self.active_connections.items():
            if websocket in conns:
                conns.remove(websocket)

    async def broadcast_to_client(self, client_id: str, message: Dict):
        if client_id not in self.active_connections:
            return
        dead = []
        for conn in list(self.active_connections[client_id]):
            try:
                await conn.send_json(message)
            except Exception as e:
                logger.debug(f"WS broadcast error to client: {e}")
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)


class DownloadSession:
    """Represents a single download session (one user request)."""

    def __init__(self, session_id: str, client_id: str, session_name: str = "Resolving..."):
        self.session_id = session_id
        self.client_id = client_id
        self.session_name = session_name
        self.tracks: List[Dict] = []
        self.total = 0
        self.completed = 0
        self.failed = 0
        self.errors: List[Dict] = []
        self.status = "resolving"  # resolving | downloading | zipping | complete | error
        self.temp_dir = tempfile.mkdtemp(prefix=f"dbt_{session_id}_")
        self.zip_path: Optional[str] = None
        self.created_at = time.time()

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "status": self.status,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "errors": self.errors,
            "has_zip": self.zip_path is not None,
        }

    def cleanup(self):
        """Remove temp files."""
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
            if self.zip_path and os.path.exists(self.zip_path):
                os.remove(self.zip_path)
        except Exception as e:
            logger.warning(f"Cleanup failed for session {self.session_id}: {e}")


class DownloadManager:
    def __init__(self):
        self.ws_manager = ConnectionManager()
        self.spotify_service = SpotifyService()
        self.youtube_provider = YoutubeProvider()
        self.soundcloud_provider = SoundCloudProvider()
        self.sessions: Dict[str, DownloadSession] = {}

    def get_session(self, session_id: str) -> Optional[DownloadSession]:
        return self.sessions.get(session_id)

    async def send_log(self, session_id: str, message: str, level: str = "info"):
        """Broadcast live log event to WebSocket clients and log to server console."""
        log_entry = {
            "type": "log",
            "session_id": session_id,
            "message": message,
            "level": level,
            "timestamp": time.strftime("%H:%M:%S"),
        }
        if level == "error":
            logger.error(f"[Session {session_id}] {message}")
        elif level == "warning":
            logger.warning(f"[Session {session_id}] {message}")
        else:
            logger.info(f"[Session {session_id}] {message}")

        try:
            session = self.get_session(session_id)
            if session:
                await self.ws_manager.broadcast_to_client(session.client_id, log_entry)
        except Exception as e:
            logger.debug(f"Failed to broadcast log: {e}")

    async def start_session(self, url: str, client_id: str, spotify_token: Optional[str] = None, sc_oauth_token: Optional[str] = None, youtube_cookie: Optional[str] = None, allow_long_tracks: bool = False) -> str:
        """
        Start a download session asynchronously. Returns session_id immediately.
        Progress and logs are streamed over WebSocket.
        """
        session_id = str(uuid.uuid4())[:8]
        url = url.strip()

        session = DownloadSession(session_id, client_id, "Resolving URL...")
        self.sessions[session_id] = session

        try:
            # Broadcast session start immediately
            await self.ws_manager.broadcast_to_client(client_id, {
                "type": "session_start",
                "session_id": session_id,
                "session_name": session.session_name,
                "status": "resolving",
                "total": 0,
                "tracks": [],
            })
            await self.send_log(session_id, f"Session initialized. Resolving URL: {url}", "info")
        except Exception as e:
            logger.warning(f"Initial WS broadcast exception (non-fatal): {e}")

        # Process URL resolution and downloads in background task
        asyncio.create_task(self._process_session(session, url, spotify_token, sc_oauth_token, allow_long_tracks))

        return session_id

    async def _process_session(self, session: DownloadSession, url: str, spotify_token: Optional[str], sc_oauth_token: Optional[str] = None, allow_long_tracks: bool = False):
        """Background handler for URL resolution and downloading."""
        try:
            tracks, session_name = await self._resolve_url(session, url, spotify_token, sc_oauth_token)
        except Exception as e:
            session.status = "error"
            session.errors.append({"track": url, "error": str(e)})
            await self.send_log(session.session_id, f"URL resolution failed: {str(e)}", "error")
            await self.ws_manager.broadcast_to_client(session.client_id, {
                "type": "session_error",
                "session_id": session.session_id,
                "error": str(e),
            })
            return

        if not tracks:
            session.status = "error"
            session.errors.append({"track": url, "error": "No tracks found"})
            await self.send_log(session.session_id, "No downloadable tracks found for this URL", "error")
            await self.ws_manager.broadcast_to_client(session.client_id, {
                "type": "session_error",
                "session_id": session.session_id,
                "error": "No tracks found for this URL",
            })
            return

        # Apply global duration filter (discard tracks > 420 seconds)
        filtered_tracks = []
        for t in tracks:
            duration = t.get("duration")
            if duration is not None and not allow_long_tracks:
                try:
                    duration_val = float(duration)
                    if duration_val > 420:
                        mins = int(duration_val // 60)
                        secs = int(duration_val % 60)
                        await self.send_log(session.session_id, f"Duration limit: Skipping '{t.get('title')}' ({mins}:{secs:02d} > 7:00)", "warning")
                        continue
                except (ValueError, TypeError):
                    pass
            filtered_tracks.append(t)

        if not filtered_tracks:
            session.status = "error"
            session.errors.append({"track": "All", "error": "All tracks exceeded 7-minute duration limit"})
            await self.send_log(session.session_id, "No downloadable tracks remain after duration filtering", "error")
            await self.ws_manager.broadcast_to_client(session.client_id, {
                "type": "session_error",
                "session_id": session.session_id,
                "error": "All tracks exceeded 7-minute duration limit",
            })
            return

        tracks = filtered_tracks
        session.tracks = tracks
        session.session_name = session_name
        session.total = len(tracks)
        session.status = "downloading"

        await self.send_log(
            session.session_id,
            f"Resolved '{session_name}' with {len(tracks)} track(s). Starting downloads...",
            "success"
        )

        await self.ws_manager.broadcast_to_client(session.client_id, {
            "type": "session_resolved",
            "session_id": session.session_id,
            "session_name": session_name,
            "total": len(tracks),
            "tracks": [{"title": t.get("title", "?"), "artist": t.get("artist", "?")} for t in tracks],
        })

        # Client will start downloading tracks individually
        session.status = "ready"
        await self.send_log(session.session_id, "Ready for client extraction.", "info")

    async def _resolve_url(self, session: DownloadSession, url: str, spotify_token: Optional[str], sc_oauth_token: Optional[str] = None) -> tuple:
        """Resolve a URL to a list of tracks and a session name."""

        # Spotify URLs
        if "spotify.com/" in url:
            if not spotify_token:
                raise ValueError("Spotify sign-in required to download Spotify links.")
            await self.send_log(session.session_id, "Connecting to Spotify API...", "info")
            data = self.spotify_service.resolve_url(url, spotify_token)
            await self.send_log(session.session_id, f"Fetched Spotify playlist '{data['name']}'", "info")
            return data["tracks"], data["name"]

        # SoundCloud URLs
        if "soundcloud.com/" in url:
            session_name = url.rstrip('/').split('/')[-1]
            if session_name in ('tracks', 'sets', 'reposts', 'likes', 'albums'):
                session_name = url.rstrip('/').split('/')[-2]

            parts = [p for p in url.rstrip('/').split('/') if p]
            parts = [p for p in parts if p not in ('https:', 'http:', '', 'soundcloud.com', 'www.soundcloud.com')]

            # Single track check
            if len(parts) == 2 and 'sets' not in parts:
                await self.send_log(session.session_id, "Identified single SoundCloud track URL", "info")
                return [{
                    "id": f"sc_direct_{int(time.time())}",
                    "title": session_name.replace('-', ' ').title(),
                    "artist": parts[0].replace('-', ' ').title(),
                    "_sc_direct_url": url,
                }], session_name.replace('-', ' ').title()

            # Profile or playlist - pass main thread asyncio event loop to thread safe logger
            await self.send_log(session.session_id, f"Enumerating SoundCloud profile/playlist at {url}...", "info")

            loop = asyncio.get_running_loop()

            def sc_logger(msg: str):
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.send_log(session.session_id, f"[SoundCloud] {msg}", "info"),
                        loop
                    )
                except Exception as e:
                    logger.warning(f"sc_logger failed: {e}")

            sc_tracks = await get_profile_tracks(url, log_callback=sc_logger, sc_oauth_token=sc_oauth_token)
            if not sc_tracks:
                raise ValueError(f"No tracks found at {url}")

            tracks = []
            for i, t in enumerate(sc_tracks):
                tracks.append({
                    "id": f"sc_{t['id']}_{i}",
                    "title": t['title'],
                    "artist": t['uploader'],
                    "duration": t.get('duration', 0),
                    "_sc_direct_url": t['url'],
                })

            return tracks, session_name.replace('-', ' ').title()

        # YouTube URLs
        if "youtube.com/" in url or "youtu.be/" in url:
            session_name = "YouTube Download"
            await self.send_log(session.session_id, "Resolving YouTube URL...", "info")

            tracks = [{
                "id": f"yt_direct_{int(time.time())}",
                "title": "YouTube Video",
                "artist": "YouTube",
                "_yt_direct_url": url,
            }]
            return tracks, session_name

        raise ValueError("Unsupported URL format. Please provide a Spotify, SoundCloud, or YouTube link.")

    def _generate_netscape_cookies(self, raw_cookie_str: str, output_path: str):
        """Convert a raw HTTP cookie string into a Netscape cookies.txt format."""
        if raw_cookie_str.lower().startswith("cookie:"):
            raw_cookie_str = raw_cookie_str[7:].strip()
            
        with open(output_path, "w", newline='\n', encoding='utf-8') as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# This file is generated by Audio Downloader\n\n")
            for cookie in raw_cookie_str.split(';'):
                cookie = cookie.strip()
                if not cookie:
                    continue
                if '=' in cookie:
                    name, value = cookie.split('=', 1)
                    f.write(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}\n")
                    f.write(f".google.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}\n")

    async def extract_single_track(self, session: DownloadSession, track: Dict, track_index: int, sc_oauth_token: Optional[str] = None, youtube_cookie: Optional[str] = None) -> Optional[str]:
        """Download a single track and return its ephemeral MP3 file path."""
        track_title = track.get("title", "Unknown Track")

        async def on_progress(info: Dict):
            await self.ws_manager.broadcast_to_client(session.client_id, {
                "type": "track_progress",
                "session_id": session.session_id,
                "track_index": track_index,
                "progress": info.get("progress", 0),
                "speed": info.get("speed", ""),
                "state": info.get("state", "downloading"),
            })

        track_dir = os.path.join(session.temp_dir, f"track_{track_index}")
        os.makedirs(track_dir, exist_ok=True)

        cookies_file = None
        if youtube_cookie:
            cookies_file = os.path.join(session.temp_dir, "youtube_cookies.txt")
            if not os.path.exists(cookies_file):
                self._generate_netscape_cookies(youtube_cookie, cookies_file)

        # Direct SoundCloud URL
        if "_sc_direct_url" in track:
            sc_url = track["_sc_direct_url"]
            if '/sets/' in sc_url:
                await self.send_log(session.session_id, f"SoundCloud set link found, finding YouTube match for '{track_title}'", "info")
                return await self._download_via_youtube_search(session, track, track_dir, track_index, on_progress, cookies_file)

            result = {"metadata": {"url": sc_url}, "download_link": sc_url}
            await self.send_log(session.session_id, f"Executing direct SoundCloud download for '{track_title}'...", "info")
            success = await self.soundcloud_provider.download(result, track_dir, progress_callback=on_progress, sc_oauth_token=sc_oauth_token)
            if not success:
                track_artist = track.get("artist", "")
                await self.send_log(
                    session.session_id,
                    f"Direct webpage URL unavailable. Attempting direct SoundCloud API stream query (scsearch1) for '{track_artist} - {track_title}'...",
                    "warning"
                )
                success = await self.soundcloud_provider.download_by_search(
                    track_artist,
                    track_title,
                    track_dir,
                    progress_callback=on_progress,
                    sc_oauth_token=sc_oauth_token
                )
            if not success:
                await self.send_log(
                    session.session_id,
                    f"SoundCloud direct stream unavailable for '{track_title}'. Matching on YouTube...",
                    "warning"
                )
                return await self._download_via_youtube_search(session, track, track_dir, track_index, on_progress, cookies_file)
            
            files = os.listdir(track_dir)
            if files:
                return os.path.join(track_dir, files[0])
            return None

        # Direct YouTube URL
        if "_yt_direct_url" in track:
            yt_url = track["_yt_direct_url"]
            cmd = [
                "yt-dlp", "-f", "bestaudio/best",
                "--remote-components", "ejs:github", "--no-js-runtimes", "--js-runtimes", "node",
                "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
                "--embed-thumbnail", "--add-metadata", "--newline", "--force-ipv6",
                "-o", f"{track_dir}/%(title)s.%(ext)s",
            ]
            if cookies_file:
                cmd.extend(["--cookies", cookies_file])
            cmd.append(yt_url)
            await self.send_log(session.session_id, f"Executing direct yt-dlp audio download from YouTube...", "info")
            success = await _run_ytdlp_with_progress(cmd, on_progress)
            if success:
                files = os.listdir(track_dir)
                if files:
                    return os.path.join(track_dir, files[0])
            return None

        # YouTube Search Fallback
        return await self._download_via_youtube_search(session, track, track_dir, track_index, on_progress, cookies_file)

    async def _download_via_youtube_search(
        self, session: DownloadSession, track: Dict, track_dir: str, track_index: int, on_progress, cookies_file: Optional[str] = None
    ) -> Optional[str]:
        """Search YouTube for a track using robust fallback queries and download the best match."""
        track_title = track.get("title", "Unknown")
        track_artist = track.get("artist", "")

        # Build fallback search queries
        art = (track_artist or "").strip()
        tit = (track_title or "").strip()
        clean_tit = re.sub(r'[^\w\s]', ' ', tit)
        clean_tit = ' '.join(clean_tit.split())
        clean_art = re.sub(r'[^\w\s]', ' ', art)
        clean_art = ' '.join(clean_art.split())

        queries = []
        if clean_art and not clean_tit.lower().startswith(clean_art.lower()):
            queries.append(f"{clean_art} {clean_tit}")
        else:
            queries.append(clean_tit)

        # Simplified 4-word title fallback
        words = clean_tit.split()
        if len(words) > 3:
            short_q = ' '.join(words[:4])
            if short_q not in queries:
                queries.append(short_q)

        results = []
        for search_query in queries:
            await self.send_log(session.session_id, f"Searching YouTube: '{search_query}'", "info")
            results = await self.youtube_provider.search(search_query, cookies_file)
            if results:
                break

        if not results:
            await self.send_log(session.session_id, f"No YouTube results found for '{track_title}'", "warning")
            raise Exception("No matching YouTube video found")

        best = await self._pick_best_result(session.session_id, results, track_title)
        if not best:
            await self.send_log(session.session_id, f"No suitable YouTube match for '{track_title}'", "warning")
            raise Exception("Skipped: Could not find an original track (only remixes/variants)")

        matched_title = best.get("filename") or "YouTube video"
        video_id = best["metadata"]["id"]
        yt_url = f"https://www.youtube.com/watch?v={video_id}"

        await self.send_log(session.session_id, f"Matched YouTube video: '{matched_title}' (id: {video_id})", "success")

        cmd = [
            "yt-dlp", "-f", "bestaudio/best",
            "--remote-components", "ejs:github", "--no-js-runtimes", "--js-runtimes", "node",
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            "--embed-thumbnail", "--add-metadata", "--newline", "--force-ipv6",
            "-o", f"{track_dir}/%(title)s.%(ext)s",
        ]
        if cookies_file:
            cmd.extend(["--cookies", cookies_file])
        cmd.append(yt_url)

        success = await _run_ytdlp_with_progress(cmd, on_progress)
        if success:
            files = os.listdir(track_dir)
            if files:
                return os.path.join(track_dir, files[0])
        return None

    async def _pick_best_result(self, session_id: str, results: List[Dict], original_title: str) -> Optional[Dict]:
        """Pick best YouTube result while avoiding remix/cover/live mismatches."""
        original_lower = original_title.lower()
        variant_keywords = ['remix', 'remake', 'cover', 'live', 'instrumental', 'acoustic', 'karaoke', 'slowed', 'reverb', 'sped up', 'bass boosted', 'bootleg', 'edit', 'vip', 'dub', 'mix']

        for result in results:
            result_title = (result.get("filename") or "").lower()
            is_mismatch = False
            for keyword in variant_keywords:
                if re.search(r'\b' + re.escape(keyword) + r'\b', result_title) and not re.search(r'\b' + re.escape(keyword) + r'\b', original_lower):
                    is_mismatch = True
                    await self.send_log(
                        session_id,
                        f"Skipping match '{result.get('filename')}' — variant keyword '{keyword}' not in original title",
                        "warning"
                    )
                    break

            if not is_mismatch:
                return result

        await self.send_log(session_id, f"All search results contained variant keywords. Strict mode active: rejecting.", "error")
        raise Exception("Skipped: Remix/Variant strictly rejected")

    async def cleanup_expired_sessions(self):
        now = time.time()
        expired = [sid for sid, s in self.sessions.items()
                   if now - s.created_at > SESSION_TIMEOUT_SECS]
        for sid in expired:
            self.sessions[sid].cleanup()
            del self.sessions[sid]


manager = DownloadManager()
