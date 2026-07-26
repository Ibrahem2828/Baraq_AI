# Changelog

## 1.0.0 - 2026-07-25

- خدمة FastAPI مستقلة لمحركات فاحص وخُطى ورشيد وخُلاصة وصدى.
- حسابان OpenAI مع failover أو weighted routing وcircuit breaker وميزانية شهرية.
- Responses API مع Strict JSON Schema وPydantic/domain validation.
- PostgreSQL/pgvector RAG، chunking عربي، hybrid reranking، citations وprompt-injection guard.
- Celery/Redis jobs، idempotency، progress، cancel وcredit reserve/commit/refund.
- تخزين المحاولات والتوكنات والتكلفة والمخرجات والتقييمات وDataset candidates.
- Prompt Registry بإصدارات YAML ومزامنة إلى قاعدة البيانات.
- Feedback، consent، anonymization، JSONL export وoffline evaluation foundation.
- OpenAPI contract، Docker Compose، Alembic، Prometheus، Sentry ووثائق الربط.
- Rate limiting على طلبات إنشاء وظائف AI.
