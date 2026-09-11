# التغييرات المطلوبة في Django Backend

> **DEPRECATED — 2026-08-19.** This pre-V2 rollout note is superseded by [DJANGO_AI_CONTRACT.md](DJANGO_AI_CONTRACT.md) and [INTERNAL_AUTH_V2.md](INTERNAL_AUTH_V2.md). Do not implement its legacy signing example.

**النطاق:** متطلبات تكامل Phase 1 فقط.
**ممنوع في هذه المرحلة:** تعديل تطبيق Django من هذا المستودع، أو نقل الرصيد/materialization إلى AI، أو تفعيل webhook قبل outbox Phase 4.

## النتيجة المطلوبة

Django على https://api.barraq.xn--mgbaab0cxheq.tech هو البوابة الوحيدة التي يراها تطبيق الجوال ولوحة التحكم. خدمة AI على https://ai.barraq.xn--mgbaab0cxheq.tech لا تستقبل JWT مستخدم ولا تستقبل اتصالاً مباشراً من العميل.

اقرأ وثيقة العقد الكاملة: [DJANGO_AI_CONTRACT.md](DJANGO_AI_CONTRACT.md).

## قائمة تنفيذ Django

### 1. طبقة Gateway

أضف service أو adapter في Django ينفذ ما يلي:

1. يصادق المستخدم ويفحص permissions والـ credits في Django أولاً.
2. ينشئ client_job_id ثابتاً وقابلاً للتتبع لكل طلب AI.
3. يرسل POST إلى /api/ai/v1/jobs بالتوقيع HMAC وIdempotency-Key.
4. يخزن الربط بين Django job وclient_job_id وAI internal UUID إن عاد.
5. يقرأ GET /api/ai/v1/jobs/{client_job_id} بالتوقيع HMAC عند polling.
6. يرسل POST /api/ai/v1/jobs/{client_job_id}/cancel بالتوقيع HMAC.
7. يمرر feedback إلى POST /api/ai/v1/feedback بالتوقيع HMAC.
8. يبقى هو صاحب reserve/commit/refund للرصيد وسجلات المستخدم وnotifications وmaterialization.

لا تضع عنوان AI أو secret HMAC في تطبيق الجوال.

### 2. Service authentication

أضف secrets متطابقة في Coolify لخدمتي Django وAI:

~~~dotenv
BARAQ_SERVICE_HMAC_SECRET=<same-random-secret-in-both-services>
BARAQ_DJANGO_SERVICE_ID=baraq-django
BARAQ_SERVICE_ID=baraq-ai-service
AI_SERVICE_BASE_URL=https://ai.barraq.xn--mgbaab0cxheq.tech
~~~

تنفيذ signer في Django:

~~~text
body_hash = sha256(exact_request_body).hexdigest()
canonical = timestamp + "\n" + method.upper() + "\n" + path + "\n" + body_hash
signature = hmac_sha256(secret, canonical).hexdigest()
~~~

للـ Django -> AI أرسل:

~~~http
X-Baraq-Service: baraq-django
X-Baraq-Timestamp: <unix seconds>
X-Content-SHA256: <sha256 hex>
X-Baraq-Signature: <hmac hex>
~~~

تحقق في Django من نفس الصيغة للطلبات الواردة من AI، لكن اسم caller يجب أن يكون baraq-ai-service. ارفض missing headers وclock skew أكبر من 300 ثانية وbody hash غير مطابق والتوقيع غير المطابق.

هذه آلية انتقالية V1. نفذ Phase 3 قبل التشغيل واسع النطاق لإضافة nonce وkey-id وreplay store وkey rotation.

### 3. API التي يجب أن يوفرها Django إلى AI

نفذ المسارات التالية تحت التطبيق الداخلي، وكلها HMAC-only. لا تجعلها تعتمد على JWT مستخدم أو public session.

| Method | Path | المطلوب |
|---|---|---|
| POST | /api/internal/v1/ai/webhooks/jobs/ | جهز الاستقبال idempotent؛ AI لن ترسل إليه قبل Phase 4 |
| GET | /api/internal/v1/ai/sources/{source_id}/manifest/ | ownership-checked metadata فقط |
| GET | /api/internal/v1/ai/sources/{source_id}/download/ | stream/download bytes المصدر |
| GET | /api/internal/v1/ai/collections/{collection_id}/manifest/ | collection metadata وsource ids |
| GET | /api/internal/v1/ai/users/{user_id}/context/ | learner context السلطوي |

استجابة manifest يجب أن تتضمن source_id وowner_user_id وtitle وmime_type وsize_bytes وcontent_sha256 (64 hex) وsubject_id الاختياري وmetadata. لا تعِد URL خارجي لتخزين الكائنات في Phase 1؛ تقوم AI بتنزيل bytes من endpoint Django الداخلي ثم تتحقق من الحجم والـ hash.

يجب أن يتحقق Django من ملكية source/collection/user وفق job أو user_id المطلوب، ومن أن caller هو خدمة AI. لا تثق بالـ source_id القادم من تطبيق الجوال خارج طبقة صلاحيات Django.

### 4. نموذج job في Django

أنشئ أو وسع نموذج job محلي ليحتوي، على الأقل:

| الحقل | السبب |
|---|---|
| id | UUID أو PK محلي |
| client_job_id | المعرف الذي يرسل إلى AI؛ immutable |
| user_id | المالك السلطوي |
| task_type | الاسم الرسمي فقط |
| idempotency_key | إعادة المحاولة الآمنة |
| credit_reservation_id | يبقى في Django |
| state | queued/running/completed/failed/canceled |
| ai_status_snapshot | ملاحظة polling |
| result_reference | لا تنشئ AI resource تجارياً |
| created_at/updated_at | audit |

في Phase 1 يمكن لـ Django عمل polling. لا تعتبر نتيجة AI materialized تلقائياً: طبّق transaction محلية في Django عند اكتمال polling، وتأكد أن reservation and state transition متسقان وفق سياسة المنتج.

### 5. أسماء المهام

أرسل فقط:

~~~text
fahes_generate_quiz
khota_generate_plan
rasheed_recommendations
kholasa_generate_summary
sada_transcribe_audio
~~~

المطابقة المؤقتة التي يجب إزالتها من Django قبل 2026-10-24:

| قديم | جديد |
|---|---|
| rasheed_recommend | rasheed_recommendations |
| kholasa_summarize | kholasa_generate_summary |
| sada_transcribe | sada_transcribe_audio |

لا تنشئ سجلات جديدة بالقيم القديمة.

### 6. صحة البيانات والتشغيل

- لا تمرر user JWT إلى AI.
- لا تعتمد على CORS كحماية.
- استخدم مهلة HTTP محددة وretry محدوداً على أخطاء النقل فقط، مع Idempotency-Key ثابت.
- لا تطبع HMAC header أو body حساساً في logs.
- اجعل job polling وfeedback مسجلين بـ client_job_id وrequest-id.
- لا تمرر redirects أو presigned URLs غير موثوقة إلى AI.
- health endpoints تخص Coolify/proxy الداخلي فقط؛ لا تربطها بتطبيق الجوال.
- لا تعرض Redis أو PostgreSQL أو Admin أو metrics للعامة.

## شروط القبول قبل ربط تطبيق الجوال

1. Job جديد من Django موقّع يعيد 202، وإعادة إرساله بنفس Idempotency-Key لا تنشئ job ثانياً.
2. JOB status polling لا يعمل من دون HMAC ولا يعمل من تطبيق الجوال.
3. الاسم القديم يسبب تحذيراً فقط خلال النافذة الانتقالية ويُخزن الاسم الرسمي.
4. AI لا تستدعي credits أو materialization في Django.
5. Source manifest/download ينجحان فقط بهوية baraq-ai-service وتتحقق AI من SHA-256.
6. secrets موجودة فقط في Coolify secret store.
7. webhook يبقى معطلاً حتى Phase 4.

## مراحل لاحقة لازمة

- **Phase 2:** dependencies مقفلة وAlembic migrations، بما فيها enum values الفعلية في PostgreSQL.
- **Phase 3:** HMAC V2 ومفاتيح قابلة للتدوير وreplay protection.
- **Phase 4:** state machine وoutbox/webhook materialization.
- **Phase 5:** object storage وSSRF/download hardening.
- **Phase 10:** Coolify network policy وreadiness/metrics/release controls.
