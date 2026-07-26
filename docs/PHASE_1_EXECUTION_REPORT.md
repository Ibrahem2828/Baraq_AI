# تقرير تنفيذ Phase 1 — Django Gateway Contract

**التاريخ:** 2026-07-26
**نطاق التنفيذ:** Phase 1 فقط، امتثالاً لطلب الملف المرفق.
**الحالة النهائية:** **BLOCKED — لا يجوز وسمها كـ production-ready أو رفعها بعد.**

## Git baseline

تم تنفيذ شرط baseline قبل تعديل الكود:

| بند | النتيجة |
|---|---|
| Git repository | تم التهيئة |
| Baseline commit | 5047c14 — chore: establish AI production baseline |
| Baseline tag | ai-baseline-phase0 |
| فحص أسرار أولي | لم يظهر secret فعلي؛ ظهرت placeholders ووثائق فقط |
| حالة الـ working tree عند baseline | نظيفة |

تم إنشاء commit مستقل لـ Phase 1 بعنوان feat: establish Django gateway contract phase 1. لم يُنشأ tag Phase 1 لأن بوابة المرحلة الكاملة لا تزال محجوبة بفشل suite الكامل لغياب الاعتمادات. لا يتم إنشاء release tag باسم نجاح Phase 1 قبل اجتياز البوابة.

## ما نُفذ

### 1. Django هو البوابة الوحيدة

- أزيل مسار JWT/JWKS للمستخدم من مسار الإنتاج في AI.
- أصبحت jobs وfeedback تعتمد HMAC لخدمة Django فقط.
- ألغيت مسارات characters المباشرة من router الإنتاجي واستبدلت بstub غير معرّض.
- ألغيت API الإدارة المكشوفة من router الإنتاجي واستبدلت بstub غير معرّض.
- أزيل endpoint metrics العام مؤقتاً؛ metrics المحمي يؤجل إلى Phase 10.
- لم يعد تطبيق الجوال مساراً مقبولاً إلى AI.

### 2. عقد jobs موحد

المسارات المنتجة هي فقط:

| Method | Path |
|---|---|
| POST | /api/ai/v1/jobs |
| GET | /api/ai/v1/jobs/{job_id} |
| POST | /api/ai/v1/jobs/{job_id}/cancel |
| POST | /api/ai/v1/feedback |
| GET | /api/ai/v1/health/live |
| GET | /api/ai/v1/health/ready |

يستخدم Django client_job_id وIdempotency-Key. الاستعلام والإلغاء يعملان بمعرف Django نفسه، لا UUID داخلي من العميل.

### 3. ملكية الأعمال

- أزيلت reserve_credits وcommit_credits وrefund_credits من JobService وBackendClient وjob processor.
- أزيل materialize المباشر من job processor.
- لا يوجد webhook مباشر من AI؛ Phase 4 ستضيف outbox دائم قبل أي callback.
- تبقى AI مسؤولة فقط عن معالجة AI وحفظ output التقني والحالة المحلية المؤقتة.
- تبقى Django مسؤولة عن الرصيد وmaterialization وnotification وسجل المستخدم.

### 4. أسماء المهام

القيم الرسمية أصبحت:

~~~text
fahes_generate_quiz
khota_generate_plan
rasheed_recommendations
kholasa_generate_summary
sada_transcribe_audio
~~~

تمت إضافة mapping انتقالي واختبارات له:

| قديم | جديد | الإزالة |
|---|---|---|
| rasheed_recommend | rasheed_recommendations | 2026-10-24 |
| kholasa_summarize | kholasa_generate_summary | 2026-10-24 |
| sada_transcribe | sada_transcribe_audio | 2026-10-24 |

يصدر الإدخال القديم DeprecationWarning وتحذيراً منظماً، ثم تتحول القيمة إلى الرسمية قبل إنشاء job جديد.

### 5. عقد Django الداخلي

تم توحيد endpoints التي تستدعيها AI إلى prefix التالي:

~~~text
/api/internal/v1/ai/
~~~

وتشمل source manifest/download وcollection manifest وlearner context، مع endpoint webhook محجوز لـ Phase 4. لم تعد AI تستخدم presigned download URL من manifest في Phase 1؛ تطلب download من Django ثم تتحقق من الحجم وSHA-256.

## المستندات المضافة

- [DJANGO_AI_CONTRACT.md](DJANGO_AI_CONTRACT.md): العقد، headers، HMAC، payloads، المسارات، Coolify، والحدود المرحلية.
- [BACKEND_REQUIRED_CHANGES.md](BACKEND_REQUIRED_CHANGES.md): التغييرات المطلوبة في Django من دون تعديل مستودع Django.
- tests/contract/: اختبارات mapping والمسارات والعقد الداخلي.

## التحقق المنفذ

| الفحص | النتيجة | الدليل |
|---|---|---|
| Python syntax | PASS | python -m compileall -q app scripts tests |
| patch whitespace | PASS | git diff --check |
| Phase 1 contract tests | PASS | 13 passed عبر python -m pytest -q tests\contract |
| منع credits/materialize/JWT production paths | PASS | static forbidden-path scan |
| full test suite | BLOCKED | ModuleNotFoundError: docx |
| OpenAPI runtime generation | BLOCKED | pydantic-settings غير مثبتة |
| Ruff | BLOCKED | launcher موجود لكن ruff executable غير موجود |
| Mypy | BLOCKED | module غير مثبت |
| Container build/runtime | لم يعاد تشغيله في Phase 1 | Docker dependency/release gate يؤجل إلى Phase 2/10 |

### تفاصيل فشل الاختبار الكامل

الأمر python -m pytest -q توقف أثناء collection في tests/test_chunker.py لأن البيئة الحالية تفتقد python-docx:

~~~text
ModuleNotFoundError: No module named 'docx'
~~~

وقبل تحويل اختبار contract إلى static contract كان runtime import يتوقف أيضاً بسبب غياب pydantic-settings. هذا ليس خطأ تم إصلاحه في Phase 1؛ هو دليل مباشر على أن dependency lock/install في Phase 2 شرط سابق للإعلان عن الجاهزية.

## بوابة Phase 1

| شرط | الحالة |
|---|---|
| عقد واحد موثق Django ↔ AI | PASS |
| أسماء task الرسمية وتوافق القديم | PASS على مستوى الكود والاختبارات |
| لا credits/materialize في AI | PASS على مستوى الكود |
| لا API عميل مباشر production | PASS على مستوى router |
| لا metrics public | PASS على مستوى التطبيق |
| tests كاملة تمر | **BLOCKED** |
| OpenAPI فعلي متحقق | **BLOCKED** |
| PostgreSQL enum/migrations متوافقة مع الأسماء الجديدة | **BLOCKED — Phase 2** |
| HMAC V2/replay protection | **DEFERRED — Phase 3** |
| outbox/webhook/state machine | **DEFERRED — Phase 4** |
| Coolify network isolation | **DEFERRED — Phase 10** |

## قرار الرفع إلى Coolify الآن

**لا ترفع هذه النسخة كإصدار إنتاجي بعد.** Phase 1 أنجز إعادة توجيه المعمارية والعقد، لكنه لم يحقق بوابة الاختبار والاعتمادات والمigrations. رفعها الآن قد يفشل عند startup أو عند أول job لأن بيئة Python لا تملك الاعتمادات المطلوبة، كما أن قاعدة البيانات الحالية لا تملك migration مؤكدة لقيم enum الجديدة.

## التالي المسموح

ابدأ Phase 2 فقط:

1. قفل dependencies في lockfile وإضافة psycopg صراحة.
2. تثبيت الاعتمادات في بيئة نظيفة.
3. إنشاء Alembic migrations بدلاً من create_all، مع ترقية آمنة لقيم task_type.
4. تشغيل full pytest وruff وmypy وOpenAPI generation.
5. عند اجتياز البوابة، أنشئ tag للمرحلة.

لا تبدأ Phase 3 أو 4 أو 5 أو 10 قبل إنهاء Phase 2 والتحقق منها.
