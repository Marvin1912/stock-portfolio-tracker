# ── Builder stage ────────────────────────────────────────────────────────────
FROM python:3.12-alpine AS builder

WORKDIR /build

# All dependencies ship pre-built musllinux wheels — no C/Rust compilation
# needed. If a future dependency requires source compilation, add the relevant
# build packages here (e.g. gcc, musl-dev, libffi-dev, cargo).

# Copy dependency manifest and install into an isolated prefix
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && \
    pip install --prefix=/install "." --no-cache-dir

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-alpine AS runtime

WORKDIR /app

# Install only the shared libraries needed at runtime by compiled extensions.
RUN apk add --no-cache \
    libpq \
    libjpeg \
    libffi

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
