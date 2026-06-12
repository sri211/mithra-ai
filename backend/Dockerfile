FROM python:3.11-slim

WORKDIR /app

# System deps: build tools + Playwright Chromium runtime requirements
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev curl \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium browser binary during build (not runtime)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install chromium

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
