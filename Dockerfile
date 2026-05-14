FROM python:3.11-slim

# ffmpeg + curl + unzip installieren
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl unzip && \
    rm -rf /var/lib/apt/lists/*

# Deno JS Runtime (für yt-dlp YouTube Extraktion)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Downloads-Ordner anlegen
RUN mkdir -p /tmp/yt_downloads

EXPOSE $PORT

CMD gunicorn --bind 0.0.0.0:$PORT --timeout 300 --workers 2 app:app
