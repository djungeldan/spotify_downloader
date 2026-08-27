import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
import os
import re
from typing import List, Dict, Optional
from .config import config_service
import logging

logger = logging.getLogger(__name__)


class SpotifyService:
    """Spotify service using OAuth Authorization Code flow."""

    def __init__(self):
        self.cfg = config_service.get_spotify_config()

    def get_auth_url(self) -> str:
        """Generate Spotify OAuth authorization URL."""
        sp_oauth = SpotifyOAuth(
            client_id=self.cfg["client_id"],
            client_secret=self.cfg["client_secret"],
            redirect_uri=self.cfg["redirect_uri"],
            scope="playlist-read-private playlist-read-collaborative user-library-read",
            cache_handler=spotipy.cache_handler.MemoryCacheHandler()
        )
        return sp_oauth.get_authorize_url()

    def exchange_code(self, code: str) -> Dict[str, str]:
        """Exchange authorization code for access/refresh tokens."""
        sp_oauth = SpotifyOAuth(
            client_id=self.cfg["client_id"],
            client_secret=self.cfg["client_secret"],
            redirect_uri=self.cfg["redirect_uri"],
            scope="playlist-read-private playlist-read-collaborative user-library-read",
            cache_handler=spotipy.cache_handler.MemoryCacheHandler()
        )
        token_info = sp_oauth.get_access_token(code, as_dict=True)
        return {
            "access_token": token_info["access_token"],
            "refresh_token": token_info.get("refresh_token", ""),
            "expires_in": token_info.get("expires_in", 3600),
        }

    def refresh_token(self, refresh_token: str) -> Dict[str, str]:
        """Refresh an expired access token."""
        sp_oauth = SpotifyOAuth(
            client_id=self.cfg["client_id"],
            client_secret=self.cfg["client_secret"],
            redirect_uri=self.cfg["redirect_uri"],
            scope="playlist-read-private playlist-read-collaborative user-library-read",
            cache_handler=spotipy.cache_handler.MemoryCacheHandler()
        )
        token_info = sp_oauth.refresh_access_token(refresh_token)
        return {
            "access_token": token_info["access_token"],
            "refresh_token": token_info.get("refresh_token", refresh_token),
            "expires_in": token_info.get("expires_in", 3600),
        }

    def _get_client(self, token: str) -> spotipy.Spotify:
        """Create authenticated Spotify client from access token."""
        return spotipy.Spotify(auth=token)

    def extract_playlist_id(self, url: str) -> Optional[str]:
        match = re.search(r'playlist/([a-zA-Z0-9]+)', url)
        return match.group(1) if match else None

    def extract_artist_id(self, url: str) -> Optional[str]:
        match = re.search(r'artist/([a-zA-Z0-9]+)', url)
        return match.group(1) if match else None

    def extract_album_id(self, url: str) -> Optional[str]:
        match = re.search(r'album/([a-zA-Z0-9]+)', url)
        return match.group(1) if match else None

    def extract_track_id(self, url: str) -> Optional[str]:
        match = re.search(r'track/([a-zA-Z0-9]+)', url)
        return match.group(1) if match else None

    def get_playlist_tracks(self, playlist_url: str, token: str) -> Dict:
        """Fetch all tracks from a Spotify playlist."""
        sp = self._get_client(token)
        playlist_id = self.extract_playlist_id(playlist_url)
        if not playlist_id:
            raise ValueError("Invalid Spotify Playlist URL")

        playlist_info = sp.playlist(playlist_id)
        playlist_name = playlist_info['name'] if playlist_info else "Unknown Playlist"
        results = sp.playlist_items(playlist_id, additional_types=['track'])

        tracks = []
        skipped = 0
        page = 0
        first_item_logged = False
        while results:
            page += 1
            items = results.get('items', [])
            logger.info(f"Playlist page {page}: {len(items)} items, total={results.get('total')}")
            for item in items:
                if item is None:
                    logger.warning("Skipping None item in playlist")
                    skipped += 1
                    continue
                # Log the raw structure of the first item to diagnose issues
                if not first_item_logged:
                    first_item_logged = True
                    logger.info(f"  FIRST ITEM KEYS: {list(item.keys())}")
                    logger.info(f"  FIRST ITEM RAW: {str(item)[:500]}")
                track = item.get('track')
                item_type = item.get('type') or (track.get('type') if track else 'unknown')
                logger.info(f"  item type='{item_type}' track={'NOT NULL' if track else 'NULL'} is_local={track.get('is_local') if track else 'N/A'}")
                if not track:
                    skipped += 1
                    continue
                # Include local files — they have name/artists but no ID
                # is_local=True tracks are searchable by title+artist on SoundCloud
                try:
                    tracks.append(self._format_track(track))
                except Exception as ex:
                    logger.warning(f"  Failed to format track: {ex}")
                    skipped += 1
                    continue

            if results['next']:
                results = sp.next(results)
            else:
                break

        logger.info(f"Playlist '{playlist_name}': {len(tracks)} tracks loaded, {skipped} skipped (unavailable/null)")
        return {"name": playlist_name, "tracks": tracks}

    def get_artist_top_tracks(self, artist_url: str, token: str) -> Dict:
        """Fetch top tracks for a Spotify artist."""
        sp = self._get_client(token)
        artist_id = self.extract_artist_id(artist_url)
        if not artist_id:
            raise ValueError("Invalid Spotify Artist URL")

        artist_info = sp.artist(artist_id)
        artist_name = artist_info['name'] if artist_info else "Unknown Artist"
        results = sp.artist_top_tracks(artist_id)

        tracks = []
        for track in results.get('tracks', []):
            tracks.append(self._format_track(track))

        return {"name": f"{artist_name} - Top Tracks", "tracks": tracks}

    def get_album_tracks(self, album_url: str, token: str) -> Dict:
        """Fetch all tracks from a Spotify album."""
        sp = self._get_client(token)
        album_id = self.extract_album_id(album_url)
        if not album_id:
            raise ValueError("Invalid Spotify Album URL")

        album_info = sp.album(album_id)
        album_name = album_info['name'] if album_info else "Unknown Album"
        artist_name = album_info['artists'][0]['name'] if album_info.get('artists') else ""
        results = sp.album_tracks(album_id)

        tracks = []
        while results:
            for track in results['items']:
                tracks.append({
                    "id": track['id'],
                    "title": track['name'],
                    "artist": track['artists'][0]['name'] if track.get('artists') else artist_name,
                    "album": album_name,
                    "duration": track['duration_ms'] / 1000,
                    "isrc": None,
                })

            if results.get('next'):
                results = sp.next(results)
            else:
                break

        return {"name": f"{artist_name} - {album_name}", "tracks": tracks}

    def get_single_track(self, track_url: str, token: str) -> Dict:
        """Fetch a single Spotify track."""
        sp = self._get_client(token)
        track_id = self.extract_track_id(track_url)
        if not track_id:
            raise ValueError("Invalid Spotify Track URL")

        track = sp.track(track_id)
        return {"name": track['name'], "tracks": [self._format_track(track)]}

    def _format_track(self, track: Dict) -> Dict:
        """Format a Spotify API track to our standard format."""
        duration_ms = track.get('duration_ms') or 0
        artists = track.get('artists') or []
        album = track.get('album') or {}
        return {
            "id": track.get('id'),
            "title": track.get('name', 'Unknown'),
            "artist": artists[0]['name'] if artists else "Unknown",
            "album": album.get('name', ''),
            "duration": duration_ms / 1000,
            "isrc": track.get('external_ids', {}).get('isrc'),
            "is_local": track.get('is_local', False),
        }

    def resolve_url(self, url: str, token: str) -> Dict:
        """Resolve any Spotify URL to a list of tracks."""
        if '/playlist/' in url:
            return self.get_playlist_tracks(url, token)
        elif '/artist/' in url:
            return self.get_artist_top_tracks(url, token)
        elif '/album/' in url:
            return self.get_album_tracks(url, token)
        elif '/track/' in url:
            return self.get_single_track(url, token)
        else:
            raise ValueError(f"Unsupported Spotify URL format: {url}")
