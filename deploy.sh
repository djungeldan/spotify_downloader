#!/bin/bash
set -e

echo "Starting production deployment for DBT Downloader..."

echo "Pulling latest code from GitHub..."
git pull origin main

echo "Building and restarting Docker eco-system..."
docker-compose down
docker-compose up --build -d

echo "Pruning old unused Docker layers/images..."
docker image prune -af

echo "Deployment complete! Production Daemon is routing."
