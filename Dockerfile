# --- console build ---------------------------------------------------------
FROM node:22-alpine AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY console/ ./
RUN npm run build

# --- gateway ---------------------------------------------------------------
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 BAZAAR_LLM=fake BAZAAR_RAZORPAY=fake PORT=8000
COPY pyproject.toml ./
COPY bazaar ./bazaar
COPY data/synthetic ./data/synthetic
RUN pip install --no-cache-dir . && mkdir -p data/runtime
COPY --from=console /console/dist ./console/dist
# Run as a non-root user (uid 1000): required by Hugging Face Spaces, and good hardening
# everywhere else. The runtime dir the app writes to (audit log, LLM cache) is owned by it.
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn bazaar.gateway.app:default_app --factory --host 0.0.0.0 --port ${PORT}"]
