# عقد التكامل بين Django وBaraq AI

**الإصدار:** Phase 1
**التاريخ:** 2026-07-26
**الحالة:** عقد الإنتاج المستهدف؛ الاختبار التشغيلي محجوب مؤقتاً حتى تثبيت الاعتمادات في Phase 2.

## المبدأ المعماري

Django هو بوابة المنصة الوحيدة وصاحب صلاحيات المستخدم وJWT والصلاحيات والرصيد وسجلات الأعمال.

~~~text
Mobile / Dashboard
        |
        v
Django: https://api.barraq.xn--mgbaab0cxheq.tech
        |  HMAC service request
        v
AI:     https://ai.barraq.xn--mgbaab0cxheq.tech
        |  (Phase 4: persistent-outbox signed webhook)
        v
Django materializes business resources
~~~

لا يتصل تطبيق الجوال أو لوحة التحكم بخدمة AI مباشرة. لا تتحقق خدمة AI من JWT المستخدم ولا تحجز أو تخصم أو ترد الرصيد، ولا تنشئ موارد Django التجارية.

## الهوية والتوقيع

كل طلبات Django إلى AI الخاصة بالوظائف وfeedback تستخدم HMAC-SHA256. هذه صيغة **Phase 1 HMAC V1**؛ لا توجد فيها nonce/key-id أو مخزن replay بعد، وهي عناصر إلزامية في Phase 3.

المتغيرات السرية في Coolify فقط:

~~~dotenv
BARAQ_BACKEND_BASE_URL=https://api.barraq.xn--mgbaab0cxheq.tech
BARAQ_SERVICE_ID=baraq-ai-service
BARAQ_DJANGO_SERVICE_ID=baraq-django
BARAQ_SERVICE_HMAC_SECRET=<secret-random-64-bytes-or-more>
~~~

لا يوضع السر في تطبيق الجوال أو JavaScript أو Git أو السجلات.

### خوارزمية Django -> AI

1. تسلسل JSON إلى bytes مرة واحدة، ثم يُرسل **نفس** الـ bytes التي تم توقيعها.
2. body_hash = SHA256(body_bytes).hexdigest().
3. canonical = "{timestamp}\n{METHOD_UPPER}\n{PATH}\n{body_hash}".
4. signature = HMAC_SHA256(shared_secret, canonical).hexdigest().
5. أرسل الرؤوس التالية:

~~~http
X-Baraq-Service: baraq-django
X-Baraq-Timestamp: <unix epoch seconds>
X-Content-SHA256: <sha256 hex of exact request body>
X-Baraq-Signature: <hmac sha256 hex>
Idempotency-Key: <8..128 chars; POST /jobs only>
Content-Type: application/json
~~~

تتحقق AI من اسم الخدمة، عمر التوقيع (±300 ثانية)، بصمة المحتوى، والتوقيع. المسار الموقع هو path فقط، مثل /api/ai/v1/jobs؛ لا تضف query parameters إلى الطلبات الموقعة في هذا العقد.

طلبات AI إلى Django الداخلية تستخدم الخوارزمية نفسها، لكن X-Baraq-Service: baraq-ai-service.

## واجهة Django -> AI الوحيدة

كل الاستجابات تتبع envelope:

~~~json
{"success": true, "data": {}, "error": null, "meta": {}}
~~~

وعند الخطأ:

~~~json
{"success": false, "data": null, "error": {"code": "…", "message": "…", "request_id": "…"}, "meta": {}}
~~~

| Method | Path | الحماية | الغرض |
|---|---|---|---|
| POST | /api/ai/v1/jobs | HMAC + Idempotency-Key | إنشاء job من Django |
| GET | /api/ai/v1/jobs/{job_id} | HMAC | قراءة الحالة/النتيجة |
| POST | /api/ai/v1/jobs/{job_id}/cancel | HMAC | طلب إلغاء |
| POST | /api/ai/v1/feedback | HMAC | تسجيل feedback موثوق |
| GET | /api/ai/v1/health/live | تشغيلي داخلي | liveness |
| GET | /api/ai/v1/health/ready | تشغيلي داخلي | readiness DB/Redis |

نقطتا health ليستا API للمستخدم. يجب أن يسمح Coolify أو reverse proxy بالوصول الداخلي فقط ولا يمررهما تطبيق الجوال.

### POST /api/ai/v1/jobs

الرؤوس المطلوبة: رؤوس HMAC وIdempotency-Key.

~~~json
{
  "client_job_id": "django-ai-job-01JXYZ",
  "user_id": "42",
  "task_type": "fahes_generate_quiz",
  "input": {
    "source_ids": ["source-100"],
    "question_count": 10,
    "language": "ar"
  },
  "model_tier": "balanced"
}
~~~

- client_job_id ينشئه Django، ثابت في retries، وطوله 1..128.
- user_id هو المعرف السلطوي من Django؛ لا تقبله AI من عميل.
- input يطابق مخطط المهمة المعنية داخل AI.
- التكرار يعتمد على (user_id, task_type, Idempotency-Key) في Phase 1. القيد الفريد الدائم لـ client_job_id وترتيب events سيأتيان في Phase 4.

استجابة القبول (202):

~~~json
{
  "success": true,
  "data": {
    "job_id": "django-ai-job-01JXYZ",
    "ai_job_id": "7c9179c0-a555-4e57-bd27-7b2f34b0dcb9",
    "status": "queued",
    "task_type": "fahes_generate_quiz",
    "character": "fahes",
    "status_url": "/api/ai/v1/jobs/django-ai-job-01JXYZ",
    "estimated_wait_seconds": 5,
    "cache_hit": false
  },
  "error": null,
  "meta": {}
}
~~~

### GET /api/ai/v1/jobs/{job_id}

job_id هو client_job_id الصادر من Django، وليس UUID داخلياً يختاره العميل. تعيد AI الحالة والتقدم والنتيجة إن اكتملت. يجب أن تحفظ Django الارتباط بين job المحلي وclient_job_id.

### POST /api/ai/v1/jobs/{job_id}/cancel

طلب idempotent. الحالات النهائية completed وfailed وcanceled تعاد كما هي. الإلغاء لا ينفذ refund في AI؛ Django وحده يقرر سياسة الرصيد.

### POST /api/ai/v1/feedback

~~~json
{
  "user_id": "42",
  "output_id": "a1c0d7a0-a555-4e57-bd27-7b2f34b0dcb9",
  "rating": 4,
  "is_helpful": true,
  "issue_types": [],
  "comment": null,
  "corrected_output": null,
  "consent_for_training": false,
  "implicit_signals": {}
}
~~~

لا ترسل AI إشعارات للجوال بسبب feedback أو نتيجة job.

## أسماء المهام

القيم الرسمية الوحيدة التي تُنشأ بها jobs جديدة:

~~~text
fahes_generate_quiz
khota_generate_plan
rasheed_recommendations
kholasa_generate_summary
sada_transcribe_audio
~~~

تقبل AI توافقاً انتقالياً عند الإدخال فقط، مع DeprecationWarning وسجل تحذيري، ثم تحفظ القيمة الرسمية:

| الاسم القديم | الاسم الرسمي |
|---|---|
| rasheed_recommend | rasheed_recommendations |
| kholasa_summarize | kholasa_generate_summary |
| sada_transcribe | sada_transcribe_audio |

تاريخ إزالة التوافق: **2026-10-24**. يجب أن يرسل Django الاسم الرسمي قبل هذا التاريخ. أسماء ملفات prompt الداخلية القديمة ليست أسماء task مخزنة.

## واجهة AI -> Django الداخلية

هذه endpoints يجب أن تنفذ في Django، مع HMAC والتحقق من baraq-ai-service:

| Method | Path | المخرج/الغرض |
|---|---|---|
| POST | /api/internal/v1/ai/webhooks/jobs/ | endpoint محجوز لoutbox Phase 4 |
| GET | /api/internal/v1/ai/sources/{source_id}/manifest/ | metadata + SHA-256 وحجم المصدر |
| GET | /api/internal/v1/ai/sources/{source_id}/download/ | bytes المصدر عبر Django |
| GET | /api/internal/v1/ai/collections/{collection_id}/manifest/ | metadata collection وsource ids |
| GET | /api/internal/v1/ai/users/{user_id}/context/ | سياق المتعلم السلطوي |

### Source manifest

~~~json
{
  "source_id": "source-100",
  "owner_user_id": "42",
  "title": "physics.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 1048576,
  "content_sha256": "<64 lowercase hex chars>",
  "subject_id": "subject-1",
  "metadata": {}
}
~~~

لا ترسل Django URL خارجي موقّع في manifest. AI تطلب download endpoint الداخلي، ثم تتحقق من الحجم وSHA-256 قبل الاستخراج. حماية SSRF وredirect streaming التفصيلية تؤجل إلى Phase 5.

### Webhook

AI **لا ترسل webhook حالياً**؛ Phase 1 يستخدم polling الموقّع من Django. في Phase 4 فقط، بعد إنشاء ai_outbox_events ومعالج delivery متين، سترسل AI events موقعة إلى endpoint أعلاه. يجب أن يكون Django idempotent على event_id وclient_job_id وsequence_no.

## التشغيل على Coolify

- خدمة AI: https://ai.barraq.xn--mgbaab0cxheq.tech
- Django gateway: https://api.barraq.xn--mgbaab0cxheq.tech
- لا تضف Authorization: Bearer <user JWT> إلى AI؛ لا تستخدم AI JWKS.
- لا تنشر Redis أو PostgreSQL أو منفذ AI الداخلي مباشرة على الإنترنت.
- ابدأ Django وAI بنفس secret service HMAC من Coolify secrets، مع Service IDs المعروضة أعلاه.
- لا تجعل CORS بديلاً عن حماية الخدمة؛ لا يوجد متصفح عميل مخول بمسارات AI.
- لا تفعل webhook قبل اكتمال Phase 4.

## عناصر مؤجلة عمداً

- HMAC V2: key-id وnonce وreplay store وkey rotation: Phase 3.
- migration Alembic للقيم الجديدة وقيود client job وoutbox وstate machine: Phase 2 ثم Phase 4.
- transactional outbox وretries وdelivery receipts: Phase 4.
- hardening تنزيل المصادر: Phase 5.
- عزل الشبكة وhealth/metrics production policy في Coolify: Phase 10.
