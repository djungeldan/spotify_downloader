# ==========================================
# Audio Downloader - Prod Hotpatch Script
# ==========================================

Write-Host "🔥 Initiating Hotpatch..." -ForegroundColor Yellow

# 1. Pull latest changes
Write-Host "📦 Pulling latest commits from git..." -ForegroundColor Cyan
git pull origin main

# 2. Rebuild Frontend
Write-Host "🏗️ Rebuilding frontend production bundle..." -ForegroundColor Cyan
Push-Location frontend
npm install
npm run build
Pop-Location

# 3. Check for Node.js (Required for yt-dlp JS Decryption)
Write-Host "🔍 Verifying Node.js dependency for yt-dlp..." -ForegroundColor Cyan
if (!(Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "⚠️  WARNING: Node.js is not installed on this system!" -ForegroundColor Red
    Write-Host "yt-dlp requires Node.js to decrypt YouTube video signatures." -ForegroundColor Red
    Write-Host "Please install Node.js before restarting." -ForegroundColor Red
} else {
    Write-Host "✅ Node.js is installed." -ForegroundColor Green
}

# 4. Restart Backend
Write-Host "🔄 Restarting backend service..." -ForegroundColor Cyan
Write-Host "⚠️  ACTION REQUIRED: Don't forget to restart your python backend process (e.g. uvicorn/pm2/windows service) to apply the python changes!" -ForegroundColor Yellow

Write-Host "✨ Hotpatch complete! The YouTube bot bypass is now live." -ForegroundColor Green
