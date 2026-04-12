---
name: Project Overview
description: Core facts about the stock portfolio tracker — tech stack, open issues, and architectural decisions made at project creation
type: project
---

Greenfield FastAPI + PostgreSQL stock portfolio tracker scaffolded 2026-04-06.

**Why:** Track a personal/team stock portfolio with automated monthly reports and broker PDF import.

**Tech stack chosen:**
- Python 3.12+, FastAPI, SQLAlchemy 2.x async (asyncpg), Alembic, Pydantic v2, pydantic-settings
- `pyproject.toml` with `hatchling` build backend; dependency management via `uv` or `poetry`
- PostgreSQL (docker-compose.yml ships postgres:16-alpine for local dev)
- APScheduler for scheduled jobs (monthly report cron)
- aiosmtplib for async SMTP email delivery
- pdfplumber + pypdf for broker PDF parsing
- JWT auth via python-jose + passlib[bcrypt]
- Ruff (lint+format) + mypy (strict) as dev tools
- pytest + pytest-asyncio + httpx for testing

**Open issues to implement next:**
- #16 — broker PDF parser
- #17 — PDF import UI
- #19 — monthly report generation
- #20 — email delivery via SMTP
- #21 — APScheduler integration

**Key architectural decisions:**
- App factory pattern: `create_app(settings)` in `app/main.py` so tests inject custom Settings
- Database init/teardown in FastAPI lifespan (not at module import time)
- Alembic reads `DATABASE_SYNC_URL` (psycopg2) from environment — never from alembic.ini
- `app/models/__init__.py` must import every model module so `Base.metadata` is complete for Alembic
- `/health` is a liveness probe — no DB I/O, always returns `{"status": "ok"}`
- Two DB URL env vars: `DATABASE_URL` (asyncpg, app) and `DATABASE_SYNC_URL` (psycopg2, alembic)

**How to apply:** When adding new models, import them in `app/models/__init__.py`. When adding new routes, register them in `app/main.py` under `/api/v1/`. Follow the service → repository → router layering already established.
