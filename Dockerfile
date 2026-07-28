FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    DATABASE_PATH=/app/data/meme_ops.db

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY . /app
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
