FROM python:3.12-slim

# ffmpeg for video processing, Firefox for cookie extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    firefox-esr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000
EXPOSE 10000

CMD gunicorn -w 2 --threads 4 --timeout 120 -b 0.0.0.0:$PORT app:app
