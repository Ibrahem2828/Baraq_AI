# Changelog

## 1.1.0 - 2026-09-03

- تفعيل Rate limiting فعلياً على `POST /jobs` (كان مُعرّفاً في الإعدادات بلا تنفيذ)؛ نافذة Redis ثابتة لكل مستخدم حسب `AI_REQUESTS_PER_MINUTE`.
- تفعيل فحص الاعتدال (`moderation`) فعلياً على كل مخرجات النماذج قبل حفظها، عبر `omni-moderation-latest` (مجاني)؛ يُسجَّل في `security_flags` ولا يوقف الإنتاج عند تعطّل الفحص نفسه.
- تحديث `OPENAI_TRANSCRIPTION_MODEL` الافتراضي إلى `gpt-4o-mini-transcribe` لصدى (نصف تكلفة `gpt-4o-transcribe` بجودة مكافئة للصوت الصفي الواضح)؛ التفريغ بمتحدثين (`diarize`) يبقى عند الطلب فقط.
- تعبئة `config/pricing.yaml` بأسعار OpenAI وGemini الحقيقية والمُتحقَّقة (بدل القيم الصفرية المؤقتة) - يُفعِّل هذا فعلياً سقوف الميزانية الشهرية لأول مرة.
- إعادة صياغة قوالب البرومبت الخمسة (فاحص، خُلاصة، خُطى، رشيد، صدى) بمعايير أقوى لجودة القياس التربوي، الأمانة العلمية، ومقاومة حقن التعليمات.
- تنظيف `.env` من مفاتيح DeepSeek غير المستخدمة (كود المزوّد غير موجود أصلاً) وتجهيزه لإدخال مفتاح OpenAI حقيقي مباشرة.

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
