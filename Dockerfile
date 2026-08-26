# Stage 1: Build Frontend
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
# Copy dependency definitions
COPY frontend/package.json ./
# Install dependencies
RUN npm install
# Copy source code
COPY frontend/ ./
# Build the React app
RUN npm run build

# Stage 2: Setup Backend
FROM python:3.11-slim

# Install system dependencies (ffmpeg is required for yt-dlp audio conversion)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Backend Code
COPY backend/ .

# Copy Built Frontend from Stage 1
COPY --from=frontend-build /app/frontend/dist /app/static

# Expose port
EXPOSE 3000

# Command to run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
