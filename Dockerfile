FROM python:3.11-slim

WORKDIR /app

# Chromium powers the opt-in Application Agent.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["python", "-u", "app.py"]
