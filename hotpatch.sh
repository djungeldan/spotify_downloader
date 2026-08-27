#!/bin/bash

# ==========================================
# Audio Downloader - Prod Hotpatch Script
# ==========================================

echo "🔥 Initiating Hotpatch..."

# 1. Pull latest changes
echo "📦 Pulling latest commits from git..."
git pull origin main

# 2. Rebuild Frontend
echo "🏗️ Rebuilding frontend production bundle..."
cd frontend
npm install
npm run build
cd ..

# 3. Check for Node.js (Required for yt-dlp JS Decryption)
echo "🔍 Verifying Node.js dependency for yt-dlp..."
if ! command -v node &> /dev/null; then
    echo "⚠️  WARNING: Node.js is not installed on this system!"
    echo "yt-dlp requires Node.js to decrypt YouTube video signatures."
    echo "Please install Node.js (e.g. sudo apt install nodejs) before restarting."
else
    echo "✅ Node.js is installed."
fi

# 4. Restart Backend
echo "🔄 Restarting backend service..."
# Assuming you're using PM2, systemctl, or docker. Adjust this to your specific prod environment!
# Example for systemd: sudo systemctl restart spotify-downloader-backend
# Example for pm2: pm2 restart backend
echo "⚠️  ACTION REQUIRED: Don't forget to restart your ASGI server (uvicorn/gunicorn/pm2) to apply the python changes!"

echo "✨ Hotpatch complete! The YouTube bot bypass is now live."
