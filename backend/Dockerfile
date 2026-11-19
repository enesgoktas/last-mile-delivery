FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

# Copy application
COPY --chown=appuser:appuser . .

# Compile Cython evaluator for the correct Python / OS (must run as root before USER switch)
RUN python app/algorithms/rich_vrp/local_search/evaluator_cpp/setup.py build_ext --inplace \
    && find app/algorithms/rich_vrp/local_search/evaluator_cpp -name '*.cpython-310-darwin.so' -delete

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Run — no --reload in production
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
