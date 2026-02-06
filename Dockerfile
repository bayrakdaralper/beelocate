FROM python:3.11-slim

# System deps for headless Chromium printing + basic fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-dejavu \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . /app

ENV PYTHONUNBUFFERED=1

# Render provides $PORT
CMD ["bash", "-lc", "gunicorn app:app --bind 0.0.0.0:${PORT:-10000} --timeout 180 --graceful-timeout 30 --workers 1 --threads 2"]
