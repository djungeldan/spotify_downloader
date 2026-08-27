from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from ..services.download_manager import manager
from ..services.config import config_service

router = APIRouter()


import os

class DownloadRequest(BaseModel):
    url: str
    client_id: str
    spotify_token: Optional[str] = None
    sc_oauth_token: Optional[str] = None
    youtube_cookie: Optional[str] = None
    allow_long_tracks: bool = False
    strict_mode: bool = True

class ExtractTrackRequest(BaseModel):
    session_id: str
    track_index: int
    sc_oauth_token: Optional[str] = None
    youtube_cookie: Optional[str] = None


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
        session_id = await manager.start_session(request.url, request.client_id, request.spotify_token, request.sc_oauth_token, request.youtube_cookie, request.allow_long_tracks, request.strict_mode)
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


@router.post("/api/extract_track")
async def extract_track(request: ExtractTrackRequest, background_tasks: BackgroundTasks):
    """Extract a single track's MP3 stream and delete it immediately after sending."""
    session = manager.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if request.track_index >= len(session.tracks) or request.track_index < 0:
        raise HTTPException(status_code=400, detail="Invalid track index")

    track = session.tracks[request.track_index]
    
    # Broadcast start so UI shows progress bar immediately
    await manager.ws_manager.broadcast_to_client(session.client_id, {
        "type": "track_start",
        "session_id": session.session_id,
        "track_index": request.track_index,
    })

    # Download the track ephemerally
    try:
        file_path = await manager.extract_single_track(session, track, request.track_index, request.sc_oauth_token, request.youtube_cookie)
    except Exception as e:
        session.failed += 1
        await manager.ws_manager.broadcast_to_client(session.client_id, {
            "type": "track_error",
            "session_id": session.session_id,
            "track_index": request.track_index,
            "track_title": track.get('title'),
            "error": str(e)
        })
        raise HTTPException(status_code=500, detail=str(e))
    
    if not file_path or not os.path.exists(file_path):
        # Broadcast failure to this track
        session.failed += 1
        await manager.ws_manager.broadcast_to_client(session.client_id, {
            "type": "track_error",
            "session_id": session.session_id,
            "track_index": request.track_index,
            "track_title": track.get('title'),
            "error": "Failed to extract MP3 stream"
        })
        raise HTTPException(status_code=500, detail="Failed to extract MP3 stream")

    # Broadcast success to this track
    session.completed += 1
    await manager.ws_manager.broadcast_to_client(session.client_id, {
        "type": "track_complete",
        "session_id": session.session_id,
        "track_index": request.track_index,
        "completed": session.completed
    })

    # Schedule deletion to keep disk space at exactly 0
    background_tasks.add_task(os.remove, file_path)

    # Return the file directly to the client as an MP3 attachment
    return FileResponse(
        file_path,
        media_type="audio/mpeg",
        filename=os.path.basename(file_path)
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

@router.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await manager.ws_manager.connect(websocket, client_id)
    try:
        while True:
            data = await websocket.receive_text()
            # Keep-alive / ping-pong
    except WebSocketDisconnect:
        manager.ws_manager.disconnect(websocket)
