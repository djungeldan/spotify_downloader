from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from .routers import api
from .services.download_manager import manager
from .services.logger import setup_logging
import os

app = FastAPI(title="DBT Downloader", version="2.0.0")

# Setup logging with WebSocket broadcasting
setup_logging(manager.ws_manager)

# CORS (Allow Frontend to hit API if running separately during dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes
app.include_router(api.router)

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "DBT Downloader"}

# Serve Frontend Static Files (if they exist - Production Mode)
static_dir = "/app/static"
if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=f"{static_dir}/assets"), name="assets")

    @app.get("/")
    async def serve_root():
        return FileResponse(os.path.join(static_dir, "index.html"))

    # SPA Fallback - serve index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = os.path.join(static_dir, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
else:
    print(f"Warning: Static directory {static_dir} not found. Frontend will not be served.")

import time
import asyncio
import tempfile

async def clean_old_zips():
    """Background daemon scanning and deleting expired ZIP files over 1 hour old in the global tmp cache."""
    while True:
        try:
            temp_dir = tempfile.gettempdir()
            current_time = time.time()
            for f in os.listdir(temp_dir):
                if f.startswith("DBT_") and f.endswith(".zip"):
                    file_path = os.path.join(temp_dir, f)
                    if os.path.isfile(file_path):
                        if current_time - os.path.getmtime(file_path) > 3600:
                            os.remove(file_path)
                            print(f"[Garbage Collector] Nuked 1-hour expired ZIP archive: {file_path}")
        except Exception:
            pass
        await asyncio.sleep(300)  # Sleep for 5 minutes before the next sweep

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(clean_old_zips())
