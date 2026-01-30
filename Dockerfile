# Dockerfile
# Container image for the sports widget (Flask + gunicorn)

FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer-less logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code + package + templates/static
COPY app.py .
COPY nhl_ticker ./nhl_ticker
COPY templates ./templates
COPY static ./static

# Optional (nice-to-have): run as non-root user
RUN useradd -m appuser
USER appuser

ENV PORT=8000
EXPOSE 8000

# app.py exposes: app = create_app()
CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app", "--workers", "2", "--threads", "4"]
