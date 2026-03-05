# YM-Navidrome migration status web server
FROM python:3.12-slim

WORKDIR /app

# Install ffmpeg (and ffprobe) for yt-dlp postprocessing
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY core/ core/
COPY util/ util/
COPY web/ web/
COPY main.py web_server.py ./

# Default port (override with PORT env)
ENV PORT=12080

EXPOSE 12080

# Run uvicorn binding to 0.0.0.0 so the server is reachable from outside the container
CMD ["sh", "-c", "uvicorn web_server:app --host 0.0.0.0 --port ${PORT}"]
