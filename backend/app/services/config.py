import os
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()


class ConfigService:
    """Manages Spotify OAuth configuration from environment variables."""

    def get_spotify_config(self) -> Dict[str, str]:
        return {
            "client_id": os.getenv("SPOTIFY_CLIENT_ID", ""),
            "client_secret": os.getenv("SPOTIFY_CLIENT_SECRET", ""),
            "redirect_uri": os.getenv("SPOTIFY_REDIRECT_URI", "http://localhost:5173/callback"),
        }

    def has_spotify_config(self) -> bool:
        cfg = self.get_spotify_config()
        return bool(cfg["client_id"] and cfg["client_secret"])


config_service = ConfigService()
