FROM python:3.11-slim

WORKDIR /app

# Install system deps for matplotlib
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --pre .

COPY . .

# Create data directory for SQLite
RUN mkdir -p /app/data

CMD ["python", "-m", "src.main"]
