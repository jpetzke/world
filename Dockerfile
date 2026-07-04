# --- Stufe 1: Frontend bauen -------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stufe 2: Python-Runtime (uv) --------------------------------------------
# uv provisioniert selbst das in pyproject geforderte Python (>=3.14).
FROM ghcr.io/astral-sh/uv:bookworm-slim
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    PYTHONUNBUFFERED=1

# Abhängigkeiten zuerst → Layer-Cache greift, solange sich der Lock nicht ändert.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Quellcode, Migrationen und gebautes Frontend. Layout muss zu den
# __file__-relativen Pfaden in api.py/db.py passen (Repo-Root == /app).
COPY README.md ./
COPY src/ ./src/
COPY db/ ./db/
COPY --from=frontend /fe/dist ./frontend/dist
RUN uv sync --frozen --no-dev

EXPOSE 8100

# Ein Worker: der In-Memory-Login-Lockout lebt im Prozess (siehe auth.py).
# --proxy-headers vertraut den X-Forwarded-* von Coolifys Traefik (TLS-Ende).
CMD ["/app/.venv/bin/uvicorn", "weltmodell.api:app", \
     "--host", "0.0.0.0", "--port", "8100", \
     "--proxy-headers", "--forwarded-allow-ips=*", "--workers", "1"]
