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
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict):
        dead = []
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception as e:
                logger.debug(f"WS broadcast error to client: {e}")
                dead.append(conn)
        for conn in dead:
            self.disconnect(conn)


class DownloadSession:
    """Represents a single download session (one user request)."""

    def __init__(self, session_id: str, session_name: str = "Resolving..."):
        self.session_id = session_id
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
            await self.ws_manager.broadcast(log_entry)
        except Exception as e:
            logger.debug(f"Failed to broadcast log: {e}")

    async def start_session(self, url: str, spotify_token: Optional[str] = None, sc_oauth_token: Optional[str] = None) -> str:
        """
        Start a download session asynchronously. Returns session_id immediately.
        Progress and logs are streamed over WebSocket.
        """
        session_id = str(uuid.uuid4())[:8]
        url = url.strip()

        session = DownloadSession(session_id, "Resolving URL...")
        self.sessions[session_id] = session

        try:
            # Broadcast session start immediately
            await self.ws_manager.broadcast({
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
        asyncio.create_task(self._process_session(session, url, spotify_token, sc_oauth_token))

        return session_id

    async def _process_session(self, session: DownloadSession, url: str, spotify_token: Optional[str], sc_oauth_token: Optional[str] = None):
        """Background handler for URL resolution and downloading."""
        try:
            tracks, session_name = await self._resolve_url(session, url, spotify_token, sc_oauth_token)
        except Exception as e:
            session.status = "error"
            session.errors.append({"track": url, "error": str(e)})
            await self.send_log(session.session_id, f"URL resolution failed: {str(e)}", "error")
            await self.ws_manager.broadcast({
                "type": "session_error",
                "session_id": session.session_id,
                "error": str(e),
            })
            return

        if not tracks:
            session.status = "error"
            session.errors.append({"track": url, "error": "No tracks found"})
            await self.send_log(session.session_id, "No downloadable tracks found for this URL", "error")
            await self.ws_manager.broadcast({
                "type": "session_error",
                "session_id": session.session_id,
                "error": "No tracks found for this URL",
            })
            return

        # Apply global duration filter (discard tracks > 420 seconds)
        filtered_tracks = []
        for t in tracks:
            duration = t.get("duration")
            if duration is not None:
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
            await self.ws_manager.broadcast({
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

        await self.ws_manager.broadcast({
            "type": "session_resolved",
            "session_id": session.session_id,
            "session_name": session_name,
            "total": len(tracks),
            "tracks": [{"title": t.get("title", "?"), "artist": t.get("artist", "?")} for t in tracks],
        })

        # Start downloads
        await self._download_all(session, sc_oauth_token)

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

    async def _download_all(self, session: DownloadSession, sc_oauth_token: Optional[str] = None):
        """Asynchronous concurrent download execution."""
        # Limit to 5 simultaneous track downloads to prevent IP throttling/bans
        semaphore = asyncio.Semaphore(5)

        async def _download_task(i: int, track: Dict):
            if session.status == "error":
                return
            
            async with semaphore:
                # Re-check error state after waiting in queue
                if session.status == "error":
                    return

                await self.send_log(
                    session.session_id,
                    f"[{i+1}/{session.total}] Downloading: '{track.get('title')}' by {track.get('artist', 'Unknown')}",
                    "info"
                )

                success = await self._download_single_track(session, track, i, sc_oauth_token)
                if success:
                    session.completed += 1
                    await self.ws_manager.broadcast({
                        "type": "track_complete",
                        "session_id": session.session_id,
                        "track_index": i,
                        "completed": session.completed
                    })
                else:
                    session.failed += 1
                    await self.ws_manager.broadcast({
                        "type": "track_error",
                        "session_id": session.session_id,
                        "track_index": i,
                        "track_title": track.get('title'),
                        "error": "Download failed"
                    })

                await self.ws_manager.broadcast({
                    "type": "session_progress",
                    "session_id": session.session_id,
                    "completed": session.completed,
                    "failed": session.failed,
                    "total": session.total,
                    "status": session.status,
                })

        # Spawn all track downloads simultaneously and wait for completion
        tasks = [_download_task(i, track) for i, track in enumerate(session.tracks)]
        await asyncio.gather(*tasks)

        # Zipping phase
        if session.completed > 0:
            session.status = "zipping"
            await self.send_log(session.session_id, "Creating ZIP archive of downloaded tracks...", "info")
            await self.ws_manager.broadcast({
                "type": "session_progress",
                "session_id": session.session_id,
                "completed": session.completed,
                "failed": session.failed,
                "total": session.total,
                "status": "zipping",
            })

            zip_filename = f"DBT_{re.sub(r'[^a-zA-Z0-9_-]', '_', session.session_name)}_{session.session_id}.zip"
            session.zip_path = os.path.join(tempfile.gettempdir(), zip_filename)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._create_zip, session.temp_dir, session.zip_path)

            session.status = "complete"
            await self.send_log(session.session_id, f"ZIP created successfully! Download ready ({session.completed} track(s)).", "success")
        else:
            session.status = "complete"
            await self.send_log(session.session_id, "All track downloads failed.", "error")

        await self.ws_manager.broadcast({
            "type": "session_complete",
            "session_id": session.session_id,
            "has_zip": session.zip_path is not None,
            "zip_filename": os.path.basename(session.zip_path) if session.zip_path else None,
            "status": "complete",
            "completed": session.completed,
            "failed": session.failed,
            "total": session.total,
        })

    async def _download_single_track(self, session: DownloadSession, track: Dict, track_index: int, sc_oauth_token: Optional[str] = None) -> bool:
        """Download a single track using the appropriate provider."""
        track_title = track.get("title", "Unknown Track")

        async def on_progress(info: Dict):
            await self.ws_manager.broadcast({
                "type": "track_progress",
                "session_id": session.session_id,
                "track_index": track_index,
                "progress": info.get("progress", 0),
                "speed": info.get("speed", ""),
                "state": info.get("state", "downloading"),
            })

        # Direct SoundCloud URL
        if "_sc_direct_url" in track:
            sc_url = track["_sc_direct_url"]
            if '/sets/' in sc_url:
                await self.send_log(session.session_id, f"SoundCloud set link found, finding YouTube match for '{track_title}'", "info")
                return await self._download_via_youtube_search(session, track, track_index, on_progress)

            result = {"metadata": {"url": sc_url}, "download_link": sc_url}
            await self.send_log(session.session_id, f"Executing direct SoundCloud download for '{track_title}'...", "info")
            success = await self.soundcloud_provider.download(result, session.temp_dir, progress_callback=on_progress, sc_oauth_token=sc_oauth_token)
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
                    session.temp_dir,
                    progress_callback=on_progress,
                    sc_oauth_token=sc_oauth_token
                )
            if not success:
                await self.send_log(
                    session.session_id,
                    f"SoundCloud direct stream unavailable for '{track_title}'. Matching on YouTube...",
                    "warning"
                )
                return await self._download_via_youtube_search(session, track, track_index, on_progress)
            return True

        # Direct YouTube URL
        if "_yt_direct_url" in track:
            yt_url = track["_yt_direct_url"]
            cmd = [
                "yt-dlp", "-f", "bestaudio/best",
                "--extract-audio",
                "--embed-thumbnail", "--add-metadata", "--newline",
                "-o", f"{session.temp_dir}/%(title)s.%(ext)s",
                yt_url,
            ]
            await self.send_log(session.session_id, f"Executing direct yt-dlp audio download from YouTube...", "info")
            return await _run_ytdlp_with_progress(cmd, on_progress)

        # YouTube Search Fallback
        return await self._download_via_youtube_search(session, track, track_index, on_progress)

    async def _download_via_youtube_search(
        self, session: DownloadSession, track: Dict, track_index: int, on_progress
    ) -> bool:
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
            results = await self.youtube_provider.search(search_query)
            if results:
                break

        if not results:
            await self.send_log(session.session_id, f"No YouTube results found for '{track_title}'", "warning")
            return False

        best = await self._pick_best_result(session.session_id, results, track_title)
        if not best:
            await self.send_log(session.session_id, f"No suitable YouTube match for '{track_title}'", "warning")
            return False

        matched_title = best.get("filename") or "YouTube video"
        video_id = best["metadata"]["id"]
        yt_url = f"https://www.youtube.com/watch?v={video_id}"

        await self.send_log(session.session_id, f"Matched YouTube video: '{matched_title}' (id: {video_id})", "success")

        cmd = [
            "yt-dlp", "-f", "bestaudio/best",
            "--extract-audio", "--audio-format", "mp3", "--audio-quality", "0",
            "--embed-thumbnail", "--add-metadata", "--newline",
            "-o", f"{session.temp_dir}/%(title)s.%(ext)s",
            yt_url,
        ]

        return await _run_ytdlp_with_progress(cmd, on_progress)

    async def _pick_best_result(self, session_id: str, results: List[Dict], original_title: str) -> Optional[Dict]:
        """Pick best YouTube result while avoiding remix/cover/live mismatches."""
        original_lower = original_title.lower()
        variant_keywords = ['remix', 'cover', 'live', 'instrumental', 'acoustic', 'karaoke', 'slowed', 'reverb', 'sped up', 'bass boosted']

        for result in results:
            result_title = (result.get("filename") or "").lower()
            is_mismatch = False
            for keyword in variant_keywords:
                if keyword in result_title and keyword not in original_lower:
                    is_mismatch = True
                    await self.send_log(
                        session_id,
                        f"Skipping match '{result.get('filename')}' — variant keyword '{keyword}' not in original title",
                        "warning"
                    )
                    break

            if not is_mismatch:
                return result

        await self.send_log(session_id, f"All search results contained variant keywords; falling back to first match", "warning")
        return results[0] if results else None

    def _create_zip(self, temp_dir: str, zip_path: str):
        """Zip all downloaded files in the session's temp directory synchronously."""
        files = []
        for f in os.listdir(temp_dir):
            filepath = os.path.join(temp_dir, f)
            if os.path.isfile(filepath):
                files.append(filepath)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filepath in files:
                arcname = os.path.basename(filepath)
                zf.write(filepath, arcname)

        # Immediately purge the raw MP3 files to preserve ephemeral storage space
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            logger.error(f"Failed to wipe ephemeral track storage in {temp_dir}: {e}")

    async def cleanup_expired_sessions(self):
        now = time.time()
        expired = [sid for sid, s in self.sessions.items()
                   if now - s.created_at > SESSION_TIMEOUT_SECS]
        for sid in expired:
            self.sessions[sid].cleanup()
            del self.sessions[sid]


manager = DownloadManager()
