# Deployment

Use `docker-compose.yml` for internal production topology: the API has no host
port, trusted forwarded IPs are configured explicitly, and worker queues match
Celery routes (`ai_interactive`, `ai_audio`, `ai_ingestion`, `ai_background`).
Use `docker-compose.dev.yml` only for the local API port mapping.

Before deploy, provide real secrets through the deployment secret store, run a
fresh disposable PostgreSQL migration, run Redis/Celery durable-outbox tests,
build the image, and run `scripts/validate_ai_release.py`. `/health/live` is
process-only; `/health/ready` checks database, Redis and the Alembic head.
