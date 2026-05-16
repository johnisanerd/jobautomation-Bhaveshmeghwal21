FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright (Chromium) + WeasyPrint + PDF rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    fonts-dejavu-core \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libharfbuzz0b \
    libfontconfig1 \
    libglib2.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    fastapi \
    uvicorn[standard] \
    supabase

# Playwright browsers (Chromium only — reduces image size)
RUN playwright install chromium --with-deps || true

# Copy source
COPY . .

# Expose FastAPI port
EXPOSE 8000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
