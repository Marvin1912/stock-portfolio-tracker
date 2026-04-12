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

# No extra apk packages needed:
#   - asyncpg has its own protocol impl and does not link libpq
#   - Pillow musllinux wheel bundles libjpeg/libfreetype/etc. in pillow.libs/
#   - libffi is already present in the python:3.12-alpine base image
# The only external system lib linked by any .so is libz, which is also
# already provided by the base image.

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
