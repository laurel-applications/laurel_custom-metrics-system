FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev libssl-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn


COPY . .

EXPOSE 80

ENV DATABASE_URL="sqlite:////app/instance/metrics.db"

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:80/health || exit 1

CMD gunicorn -b 0.0.0.0:80 --timeout 120 app:create_app
