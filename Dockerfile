# The website: the built React app served by the FastAPI API, on one port.
# The refresh pipeline (scraper, browser, narration) runs elsewhere and is not
# part of this image; the site only reads the database it writes to.
#
#   docker build -t ufc-forecast-engine .
#   docker run -p 8420:8420 -e DATABASE_URL=postgresql://... ufc-forecast-engine

FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
COPY models/ models/
COPY --from=frontend /app/frontend/dist frontend/dist

# Hosts set PORT; 8420 matches local development.
EXPOSE 8420
CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8420}"]
