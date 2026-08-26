from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from ..services.download_manager import manager
from ..services.config import config_service

router = APIRouter()


class DownloadRequest(BaseModel):
    url: str
    spotify_token: Optional[str] = None
    sc_oauth_token: Optional[str] = None


class SpotifyCallbackRequest(BaseModel):
    code: str


class SpotifyRefreshRequest(BaseModel):
    refresh_token: str


# --- Download Endpoints ---

@router.post("/api/download")
async def start_download(request: DownloadRequest, background_tasks: BackgroundTasks):
    """Start a download session for a given URL."""
    if not request.url.strip():
        raise HTTPException(status_code=400, detail="URL is required")

    try:
        # Clean up expired sessions first
        await manager.cleanup_expired_sessions()
        session_id = await manager.start_session(request.url, request.spotify_token, request.sc_oauth_token)
        return {"session_id": session_id}
    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Failed to start download session")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/download/{session_id}/status")
async def get_session_status(session_id: str):
    """Get the status of a download session."""
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@router.get("/api/download/{session_id}/zip")
async def download_zip(session_id: str):
    """Download the completed zip file for a session."""
    session = manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != "complete" or not session.zip_path:
        raise HTTPException(status_code=400, detail="Zip not ready yet")

    import os
    if not os.path.exists(session.zip_path):
        raise HTTPException(status_code=404, detail="Zip file not found")

    import re
    safe_name = re.sub(r'[^\w\s-]', '', session.session_name).strip()[:50] or "download"
    filename = f"{safe_name}.zip"

    return FileResponse(
        session.zip_path,
        media_type="application/zip",
        filename=filename,
    )


# --- Spotify OAuth Endpoints ---

@router.get("/api/spotify/auth-url")
async def get_spotify_auth_url():
    """Get the Spotify OAuth authorization URL."""
    if not config_service.has_spotify_config():
        raise HTTPException(status_code=500, detail="Spotify app credentials not configured on server")

    auth_url = manager.spotify_service.get_auth_url()
    return {"auth_url": auth_url}


@router.post("/api/spotify/callback")
async def spotify_callback(request: SpotifyCallbackRequest):
    """Exchange Spotify auth code for tokens."""
    try:
        tokens = manager.spotify_service.exchange_code(request.code)
        return tokens
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to exchange code: {str(e)}")


@router.post("/api/spotify/refresh")
async def spotify_refresh(request: SpotifyRefreshRequest):
    """Refresh an expired Spotify access token."""
    try:
        tokens = manager.spotify_service.refresh_token(request.refresh_token)
        return tokens
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to refresh token: {str(e)}")


# --- WebSocket ---

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Keep-alive / ping-pong
    except WebSocketDisconnect:
        manager.ws_manager.disconnect(websocket)
