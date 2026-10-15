# Build the tracked frontend's generated Tailwind stylesheet.
FROM node:20-bookworm-slim AS css-build
WORKDIR /app
COPY package.json package-lock.json tailwind.config.js ./
COPY frontend ./frontend
RUN npm ci && npm run build:css

# The Flask service serves both the frontend and API.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Tesseract supports PNG/JPG resume OCR. The CA bundle supports Supabase TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY --from=css-build /app/frontend/tailwind.css ./frontend/tailwind.css

RUN useradd --uid 10001 --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
# One worker keeps the job catalogue's process-local cache consistent after edits.
# Hosting platforms supply PORT; the fallback allows local Docker use.
CMD ["sh", "-c", "exec gunicorn --chdir backend app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 120"]
