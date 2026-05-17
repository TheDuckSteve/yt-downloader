FROM python:3.11-slim

# ffmpeg + nodejs installieren (Node.js fuer yt-dlp n-challenge solver)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl ca-certificates gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Downloads-Ordner anlegen
RUN mkdir -p /tmp/yt_downloads

EXPOSE $PORT

CMD gunicorn --bind 0.0.0.0:$PORT --timeout 300 --workers 2 app:app
