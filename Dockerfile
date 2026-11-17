FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev libssl-dev curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn


COPY . .

EXPOSE 80

ENV DATABASE_URL="sqlite:////app/instance/metrics.db"

CMD ["gunicorn", \
     "--bind", "0.0.0.0:80", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:create_app()"]


# Healthcheck hits the /health endpoint that the app exposes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:80/health').read()" \
    || exit 1
