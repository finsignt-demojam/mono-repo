# FinSight Audio Event Handler Container
#
# Flask application that receives S3 events and triggers KFP pipelines

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 8080

# Environment variables
ENV PORT=8080 \
    PYTHONUNBUFFERED=1

# Labels
LABEL org.opencontainers.image.source="https://github.com/finos-labs/Agent-QuickStart"
LABEL org.opencontainers.image.description="FinSight Audio Event Handler"
LABEL org.opencontainers.image.authors="FinSight Agent Team"
LABEL org.opencontainers.image.version="1.0.0"
LABEL quay.expires-after="never"

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/health')" || exit 1

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "app:app"]




