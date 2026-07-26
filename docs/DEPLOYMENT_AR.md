# النشر والتشغيل

## الخدمات

- `ai-api`: FastAPI.
- `ai-worker`: Celery Workers.
- `ai-beat`: مهام دورية.
- `postgres`: PostgreSQL 16 مع pgvector.
- `redis`: Broker/Circuit Breaker/Cache.

## Nginx

استخدم الملف `deploy/nginx_baraq_ai.conf` داخل الـReverse Proxy.

## بوابات الإصدار

قبل Production:

- Alembic migrations ناجحة.
- Health ready = ok.
- Test Suite ناجح.
- OpenAI Account Failover مجرب.
- Credit reserve/commit/refund مجرب بالتزامن.
- Signed URLs مجربة.
- P95 latency مقاس.
- Logs لا تحتوي Secrets أو ملفات كاملة.
- Backup وRestore لقاعدة AI مجربان.
