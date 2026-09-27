FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache

# OpenCV (used by Docling) needs these shared libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch keeps the image a fraction of the CUDA size.
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# Pre-download Docling's layout and table models into the image.
COPY app/demo/demo_statement.pdf app/demo/demo_statement.pdf
COPY scripts/warm_models.py scripts/warm_models.py
RUN python scripts/warm_models.py && chmod -R a+rX /opt/hf-cache

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Uploaded files live in a volume, owned by an unprivileged user; nothing there is executable.
RUN useradd --create-home --uid 10001 spendlens \
    && mkdir -p /data/uploads && chown spendlens /data/uploads && chmod 700 /data/uploads
USER spendlens

ENV STORAGE_DIR=/data/uploads
EXPOSE 8020
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]
