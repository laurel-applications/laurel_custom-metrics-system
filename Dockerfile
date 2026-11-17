FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

EXPOSE 8000

ENV DATABASE_URL="sqlite:////app/instance/metrics.db"

CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:create_app"]