# خدمة الذكاء الاصطناعي المستقلة لمنصة برّاق

هذه الحزمة هي خدمة مستقلة مبنية بـ **FastAPI + PostgreSQL/pgvector + Redis + Celery** لتشغيل محركات برّاق الخمسة:

- **فاحص**: إنشاء اختبارات موثقة بالمصادر.
- **خُطى**: إنشاء خطط دراسية ضمن قيود الوقت والأولويات.
- **رشيد**: تفسير الأداء وتقديم توصيات قابلة للقياس.
- **خُلاصة**: تلخيص المصادر التعليمية مع مراجع.
- **صدى**: تحويل الصوت إلى نص وتنظيمه تعليمياً.

## المسار الخارجي النهائي

بعد ضبط الـReverse Proxy، تكون واجهات الخدمة تحت نفس نطاق الباك الحالي:

```text
https://api.barraq.xn--mgbaab0cxheq.tech/api/ai/v1/
```

أمثلة:

```text
POST /api/ai/v1/fahes/quizzes
POST /api/ai/v1/khota/plans
POST /api/ai/v1/rasheed/recommendations
POST /api/ai/v1/kholasa/summaries
POST /api/ai/v1/sada/transcriptions
GET  /api/ai/v1/jobs/{job_id}
POST /api/ai/v1/feedback
```

## لماذا الخدمة منفصلة؟

- عزل تكلفة وأعطال الذكاء الاصطناعي عن الباك الأساسي.
- تشغيل Workers مستقلة للملفات والصوت والـEmbeddings.
- توسيع الموارد أو تغيير المزود دون إعادة نشر تطبيق Django.
- تخزين Logs وPrompts وDatasets وEvaluations ضمن قاعدة مستقلة.
- إبقاء المستخدمين والاشتراكات والدرجات والخطط النهائية ملكاً للباك الأساسي.

## حسابان مستقلان من OpenAI

تدعم الحزمة حساباً أساسياً وحساباً ثانوياً:

```env
OPENAI_PRIMARY_API_KEY=
OPENAI_PRIMARY_PROJECT_ID=
OPENAI_SECONDARY_API_KEY=
OPENAI_SECONDARY_PROJECT_ID=
PROVIDER_ROUTING_POLICY=failover
```

السياسة الافتراضية **failover**: يبدأ الطلب بالحساب الأساسي ثم ينتقل إلى الثانوي عند Rate Limit أو Timeout أو أخطاء مؤقتة أو تجاوز الميزانية المحددة. ويمكن استخدام `weighted` لتوزيع الحمل المصرح به.

> مفاتيح OpenAI لا توضع أبداً في تطبيق الجوال أو Git أو ملفات الواجهة؛ مكانها الوحيد هو Secret Manager أو ملف البيئة على الخادم.

## نقطة الإعداد المركزية

- مفاتيح الحسابين وروابط الباك والموديلات: `.env`
- توزيع المهام على الموديلات: `config/model_routing.yaml`
- أسعار حساب التكلفة: `config/pricing.yaml`
- جميع مسارات الربط مع الباك: `app/core/endpoints.py`
- البرومبتات وإصداراتها: `app/prompts/templates/*.yaml`
- عقد OpenAPI المولد: `docs/openapi.json`
- الحد الأقصى لطلبات الإنشاء لكل مستخدم: `AI_REQUESTS_PER_MINUTE`

## بدء التشغيل

```bash
cp .env.example .env
# املأ الأسرار وقيم قاعدة البيانات

docker compose up -d postgres redis

docker compose run --rm ai-api alembic upgrade head

docker compose up -d ai-api ai-worker ai-beat
```

فحص الجاهزية:

```bash
curl https://api.barraq.xn--mgbaab0cxheq.tech/api/ai/v1/health/ready
```

مزامنة نسخ البرومبتات مع قاعدة البيانات بعد migrations:

```bash
python scripts/sync_prompts.py
```

تصدير Dataset مراجعة وموافق عليها:

```bash
python scripts/export_dataset.py \
  --name fahes-ar-v1 \
  --task-type fahes_generate_quiz \
  --output exports/fahes-ar-v1.jsonl
```

## تدفق الطلب

```text
Mobile App
  -> /api/ai/v1/<character>/<action>
  -> JWT Verification
  -> Credit Reservation in Django
  -> AI Job + Idempotency
  -> Celery Worker
  -> Source Manifest + Signed Download URL
  -> Extraction + Chunking + Embeddings + pgvector Retrieval
  -> Versioned Prompt + OpenAI Responses API
  -> Strict JSON Schema + Domain Validation
  -> Materialize Result in Django
  -> Commit Credits
  -> User Feedback
  -> Anonymized Dataset Candidate
```

## الخصوصية والتدريب

- لا تدخل نتيجة المستخدم إلى Dataset لمجرد حصولها على تقييم سلبي.
- يلزم Consent صريح.
- يتم إخفاء PII وإزالة التكرار والمراجعة البشرية.
- يتم تجميد Dataset بإصدار وSHA-256 قبل التقييم أو التدريب.
- Fine-tuning لا يستبدل RAG؛ RAG يوفر معرفة الملف الحالي، بينما Fine-tuning يحسن الأسلوب والثبات والالتزام بالبنية.

## التحقق قبل الإنتاج

```bash
python scripts/validate_package.py
pytest
ruff check .
mypy app
alembic upgrade head
```

ثم نفّذ اختبارات تكامل حقيقية مع:

- PostgreSQL + pgvector.
- Redis + Celery.
- حسابي OpenAI تجريبيين.
- نسخة Staging من باك برّاق.
- ملفات PDF/DOCX/PPTX وصوت عربية واقعية.

## ملاحظة مهنية

الحزمة مصممة لتكون أساساً إنتاجياً شاملاً، لكنها لا يمكن اعتبارها خالية من الأخطاء في بيئة الإنتاج قبل تنفيذ migrations، واختبارات التكامل، واختبارات الضغط والأمن باستخدام مفاتيحك وبيئتك الفعلية.
