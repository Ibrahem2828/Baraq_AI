# AI Production Baseline — Phase 0

تاريخ الجرد: 2026-07-26  
نطاق المرحلة: **Phase 0 فقط — Inventory وBaseline دون تعديل أي مسار تشغيلي.**  
القرار المرحلي: **BLOCKED**

## 1. الملخص التنفيذي

الخدمة تملك أساسًا معماريًا جيدًا: FastAPI، طبقات خدمات واضحة، PostgreSQL/pgvector، Redis/Celery، RAG، مخرجات منظمة، ومحاولات تدقيق للكلفة والتغذية الراجعة. لكنها لا تطابق بعد معمارية **Django Gateway Architecture** الإلزامية، وتوجد عوائق تشغيلية وأمنية مؤكدة تمنع اعتبارها جاهزة حتى لـstaging دون إصلاحات منظمة.

لا تم إجراء أي تعديل على كود التشغيل في هذه المرحلة. هذه الوثيقة تسجل الحالة الفعلية وتضع بوابة الدخول إلى Phase 1.

## 2. هوية المصدر وإمكانية التتبع

| الحقل | النتيجة |
|---|---|
| Git worktree | غير متاح: المجلد الحالي ليس Git repository. |
| Branch | غير قابل للتحديد. |
| Commit hash | غير قابل للتحديد. |
| Dirty worktree | غير قابل للتحقق لغياب Git metadata. |
| أثر الإصدار | لا توجد وسيلة مؤكدة لربط هذا الجرد بـcommit أو tag. |

**Blocker تشغيلي:** لا يجوز النشر من مصدر لا يملك revision قابلًا للاسترجاع. قبل Phase 1 يجب وضع المشروع في مستودع Git خاص، وتسجيل commit/tag لكل مرحلة وإتاحة CI على هذا المصدر.

## 3. جرد المشروع

| المجال | الحالة المكتشفة |
|---|---|
| Python | 98 ملفًا، 5,232 سطرًا في `app/`, `alembic/`, `scripts/`, `tests/`. |
| الاختبارات | 6 ملفات، 11 test cases، 150 سطر اختبار. |
| API | 13 route decorators؛ ملف OpenAPI موجود ويحتوي 12 path. |
| الشخصيات | Fahes, Khota, Rasheed, Kholasa, Sada. |
| Prompts | 5 YAML templates وإدارة Prompt Registry. |
| قاعدة البيانات | SQLAlchemy async + PostgreSQL + pgvector، وmigrations عددها 2. |
| الخلفية | Celery worker واحد وCelery beat؛ المسارات الفعلية في queue واحدة `ai_default` رغم تمرير أسماء queues إضافية للعامل. |
| RAG | TXT/PDF/DOCX/PPTX extraction، chunking، embeddings، pgvector retrieval، reranker، citations. |
| Training | feedback، anonymization، candidates، export JSONL، evaluation helpers. |
| تشغيل | Dockerfile وحيد + Docker Compose من 5 خدمات. |
| التوثيق | 13 ملفًا حاليًا ضمن `docs/`، ولا يوجد `README.md` إنجليزي أعلى المشروع (يوجد `README_AR.md`). |

### مسارات الكود ذات الأثر الإنتاجي

```text
app/api/v1/              HTTP API
app/services/            jobs / generation / backend integration / ingestion
app/pipelines/           Fahes / Khota / Rasheed / Kholasa / Sada
app/providers/           OpenAI / routing / circuit breaker
app/rag/                 extraction / chunking / embedding / retrieval
app/models/              jobs / outputs / sources / feedback / datasets
app/workers/             Celery worker وbeat
app/core/                config / security / Redis / logging / errors
alembic/                 migrations
config/                  model routing وpricing
deploy/                  Nginx sample
```

## 4. الأوامر المنفذة والنتائج الدقيقة

| الأمر | النتيجة | التفسير |
|---|---|---|
| `python -m compileall -q app alembic scripts tests` | ناجح، exit 0 | يتحقق من الصياغة فقط، ولا يثبت سلامة الاعتماديات أو التشغيل. |
| `python scripts/validate_package.py` | ناجح | فحص 98 ملف Python ولم يجد أخطاء syntax أو مفتاح OpenAI بنمط `sk-...`. نطاقه محدود ولا يعد security audit كاملًا. |
| `python -m pytest` | فشل في collection | `ModuleNotFoundError: No module named 'docx'` من `app/rag/extractors.py`. لم تُنفذ أي test case. |
| `python -m ruff check .` | غير منفذ فعليًا | Ruff launcher موجود لكن binary غير متاح في البيئة الحالية. |
| `python -m mypy app` | غير منفذ | `No module named mypy`. |
| `docker compose config --no-interpolate` | ناجح | يثبت صحة Compose syntax، وكشف أن `.env` مطلوب وport `8001:8000` منشور. |
| `docker info --format '{{.ServerVersion}}'` | فشل | Docker daemon غير متاح (`dockerDesktopLinuxEngine`). لا يوجد Docker build أو startup evidence. |

### حالة بوابة الاختبارات

لا توجد أدلة تشغيلية حاليًا على نجاح:

- تثبيت اعتماديات الحزمة في بيئة نظيفة.
- unit tests أو lint أو type checking.
- Alembic على PostgreSQL حقيقية.
- pgvector وHNSW index.
- Redis/Celery.
- اتصال Django أو OpenAI.
- Docker build/startup أو Coolify deployment.

لذلك لا يصح وصف المشروع بأنه READY أو STAGING READY في هذه المرحلة.

## 5. درجة البداية

**Baseline production-readiness score: 56 / 100 (ثقة متوسطة؛ مراجعة ساكنة فقط).**

| البعد | الدرجة | سبب التقييم |
|---|---:|---|
| تصميم الخدمة وفصل المسؤوليات | 14 / 20 | طبقات واضحة وخدمات AI مفصولة، لكن حدود Django Gateway الحالية غير صحيحة. |
| جودة الكود ووضوحه | 10 / 15 | نماذج Pydantic وRAG وprovider abstraction جيدة؛ توجد broad exceptions وتغيير status موزع. |
| الأمن والخصوصية | 7 / 20 | JWT/HMAC أساس جيد، لكن لا nonce/replay prevention، download غير مقيد، retention غير مفعّل، metrics وport مكشوفان. |
| الموثوقية والبيانات | 8 / 15 | Celery وidempotency موجودان، لكن لا outbox/callback/reconciliation/state machine مركزي. |
| التشغيل والنشر | 6 / 15 | Docker/Compose موجودان، لكن dependencies غير محكمة وmigrations/prompt sync غير قابلة للتشغيل كما توثق. |
| الاختبارات والجودة القابلة للإثبات | 3 / 15 | اختبار محدود ولم يبدأ في البيئة، ولا توجد integration/staging evidence. |

## 6. نتيجة فحوص الجرد الثابتة

| الفحص | النتيجة |
|---|---|
| TODO/FIXME/NotImplemented في مسارات الإنتاج | لم يظهر TODO/FIXME/NotImplemented. ظهر `pass` فقط في template Alembic التوليدي وليس مسار تشغيل. |
| Mock/Fake/Stub provider في الإنتاج | لم يظهر marker في `app/`, `config/`, `alembic/`, `scripts/`. |
| مفاتيح OpenAI hardcoded بالنمط المفحوص | صفر. لا يعني ذلك عدم وجود كل أنواع secrets؛ إنما لا يوجد `sk-...` ظاهر. |
| حالة Job معدلة مباشرة | 7 مواضع `job.status =`؛ لا توجد state machine مركزية. |
| أسعار صفرية | موجودة لجميع النماذج في `config/pricing.yaml`. |
| public API port | موجود: `8001:8000` في Compose. |
| forwarded proxy trust | واسع: `--forwarded-allow-ips '*'`. |
| broad exception handling | موجود في API/providers/RAG/ingestion/job processor ويحتاج تصنيفًا وعلاجًا مدروسًا. |
| إعدادات retention | متغيرات معرفة، لكن لا يظهر استعمال فعلي لـraw-content retention أو audio duration أو request-text limit. |

## 7. مقارنة العقد الحالي بعقد Django Gateway المطلوب

### العقد الحالي في الخدمة

الخدمة تقبل طلبات عامة مباشرة من العميل عبر JWT/JWKS في `app/api/dependencies.py`. كما أن `app/core/endpoints.py` يطلب من Django المسارات التالية:

```text
GET  /api/internal/ai/v1/sources/{source_id}/manifest/?user_id={user_id}
GET  /api/internal/ai/v1/users/{user_id}/context/
POST /api/internal/ai/v1/credits/reserve/
POST /api/internal/ai/v1/credits/commit/
POST /api/internal/ai/v1/credits/refund/
POST /api/internal/ai/v1/materialize/
```

والـtask types الحالية هي:

```text
fahes_generate_quiz
khota_generate_plan
rasheed_recommend
kholasa_summarize
sada_transcribe
source_ingest
```

### العقد المطلوب في توجيه المشروع

المعمارية المطلوبة تجعل Django العميل الوحيد لخدمة AI، وتطلب webhooks من AI إلى Django، وتستخدم task types الرسمية:

```text
fahes_generate_quiz
khota_generate_plan
rasheed_recommendations
kholasa_generate_summary
sada_transcribe_audio
```

### فجوات P0 في العقد

1. يوجد public JWT/JWKS flow داخل AI، وهو مخالف لمبدأ Django Gateway only.
2. AI تدير `reserve/commit/refund` للـcredits، بينما المطلوب أن يقررها Django بعد تلقي webhook موثوق.
3. المسارات الحالية `/api/internal/ai/v1/...` لا تطابق الصيغة المطلوبة `/api/internal/v1/ai/...`، ولا توجد source download أو collection manifest أو job webhook.
4. لا توجد `external_job_id` / `client_job_id` فريدة في نموذج `AIJob` وفق العقد الجديد.
5. ثلاثة task type names لا تطابق الأسماء الرسمية. أي توافق يجب أن يكون mapping مؤقتًا موثقًا ومختبرًا، لا إعادة تسمية صامتة.

**قرار Phase 1:** لا تعدل Django في هذا المستودع. كل تغيير مطلوب فيه يسجل في `docs/BACKEND_REQUIRED_CHANGES.md` عند بدء Phase 1.

## 8. العوائق الحرجة المكتشفة (P0)

| ID | الدليل | الأثر | المرحلة المخصصة |
|---|---|---|---|
| P0-01 | `DATABASE_SYNC_URL` يستعمل `postgresql+psycopg` لكن `psycopg` غير معلن في `pyproject.toml`. | Alembic لن يعمل من image نظيفة. | Phase 2 |
| P0-02 | `0001_initial.py` يستخدم `Base.metadata.create_all()` داخل migration. | المخطط التاريخي غير قابل لإعادة الإنتاج. | Phase 2 |
| P0-03 | لا lockfile/constraints؛ `requirements.txt` يحوي فقط `-e .` والاعتماديات كلها نطاقات واسعة. | builds غير حتمية وقد تتغير عند كل نشر. | Phase 2 |
| P0-04 | Dockerfile لا ينسخ `scripts/`، بينما التشغيل يوثق `scripts/sync_prompts.py`. لا توجد خدمة migrate/prompt-sync. | بدء الخدمة يمكن أن يسبق schema/prompts أو لا يمكن تنفيذ الخطوات الموثقة داخل image. | Phase 2 |
| P0-05 | HMAC الحالي canonical string هو timestamp/method/path/body hash فقط. لا nonce ولا Redis replay cache ولا secret rotation. | replay vulnerability وعقد داخلي لا يطابق HMAC V2 المطلوب. | Phase 3 |
| P0-06 | `BackendClient` يستخدم `follow_redirects=True` ويحمّل `download_url` مباشرة؛ لا allowlist ولا منع private IP ولا streaming. | SSRF/redirect ومخاطر ذاكرة ومصدر غير موثوق. | Phase 5 |
| P0-07 | `SourceManifest.owner_user_id` لا تقارن بـ`job.user_id`. | خطر cross-user source access عند خلل الباك. | Phase 5 |
| P0-08 | `MAX_AUDIO_FILE_BYTES=200MB` بينما مسار Sada لا يجزئ الملف، وAudio API المستهدف له حد أصغر. | فشل مضمون للملفات المقبولة محليًا التي تتجاوز حد المزوّد. | Phase 8 |
| P0-09 | `gpt-4o-transcribe` يستقبل `verbose_json` في code؛ مسار diarization لا يستخدم `diarized_json` أو `chunking_strategy`. | Sada غير متوافق مع واجهة المزوّد الحالية. | Phase 8 |
| P0-10 | جميع قيم `pricing.yaml` تساوي `0.0` وتكلفة التفريغ تعاد صفرًا. | budgets وFinOps لا تحمي الإنفاق. | Phase 6 |
| P0-11 | لا توجد state machine مركزية، callback outbox أو reconciliation؛ تغيرات status موزعة. | jobs/callbacks قد تضيع أو تتكرر ولا توجد retry-safe materialization. | Phase 4 |
| P0-12 | `RAW_CONTENT_RETENTION_DAYS` وغيرها معرفة ولا تطبق؛ chunks والنص المستخرج لا يظهر لهما cleanup. | مخالفة retention وخصوصية الطالب. | Phase 9 |
| P0-13 | Compose ينشر `8001:8000` وUvicorn يثق بكل forwarded IPs. | تجاوز Coolify proxy/TLS ومخاطر header spoofing. | Phase 2 / 10 |
| P0-14 | API الحالية والتكامل الراجع لا يطابقان Django Gateway contract الجديد. | لا يجوز نشره إلى النطاقين قبل Contract alignment واختبار مشترك. | Phase 1 |

## 9. مخاطر P1 تحتاج المعالجة بعد فك P0

- `/metrics` متاح بدون مصادقة ما دام `PROMETHEUS_ENABLED=true`.
- `/health/ready` يرجع HTTP 200 حتى في حالة `degraded`؛ لا يصلح كبوابة readiness.
- `PyJWKClient` متزامن داخل مسار async.
- Request validation وHTTP errors لا تضمن دائمًا API envelope الموحد.
- `POST /jobs` يأخذ idempotency key في body، بينما endpoints الأخرى تستعمل header؛ العقد غير موحد.
- feedback update قد ينشئ training candidates مكررة؛ لا يوجد constraint يضمن العلاقة المطلوبة.
- ingestion المتوازي لنفس source/version لا يملك قفلًا واضحًا.
- المصدر المحمل والنصوص المستخرجة يجريان في الذاكرة، ما يرفع مخاطر RAM عند concurrency.
- الاختبارات الحالية لا تغطي contract، migrations، callbacks، Redis/Celery، source security، Sada، أو فشل المزوّد.

## 10. ما لا يمكن إثباته في Phase 0

لا تملك هذه المرحلة أدلة تؤكد أو تنفي:

- وجود JWKS والمسارات الداخلية في Django على النطاق الفعلي.
- صحة توقيع HMAC في Django أو idempotency في materialization.
- صلاحية مفاتيح OpenAI أو صلاحية الحسابين primary/secondary.
- دعم النماذج في مشروع OpenAI الفعلي وحدود الحساب.
- نجاح migration أو HNSW في PostgreSQL فعلي.
- سلوك Coolify أو DNS/TLS للنطاقين.
- SLO أو أداء أو security/load/backup/restore/canary.

هذه أمور يجب أن تثبت لاحقًا في staging، وليست افتراضات يمكن تسويقها كجاهزية إنتاجية.

## 11. بوابة الخروج من Phase 0

يسمح ببدء **Phase 1: Contract Alignment with Django** فقط بعد اعتماد النقاط التالية:

1. قبول Django Gateway Architecture كالمسار الوحيد: client -> Django -> AI -> signed webhook -> Django materialization.
2. اعتماد العقد النهائي وmapping التوافق المؤقت للأسماء القديمة مع تاريخ deprecation.
3. تسجيل كل تعديل مطلوب في Django في `docs/BACKEND_REQUIRED_CHANGES.md` دون تعديل Django من هذا المستودع.
4. تثبيت هذا الجرد في Git commit/tag عند إنشاء المستودع.
5. الحفاظ على القرار الحالي: **BLOCKED** إلى أن تزول P0 الخاصة بالعقد على الأقل.

## 12. الملفات المعدلة في Phase 0

| الملف | النوع |
|---|---|
| `docs/AI_PRODUCTION_BASELINE.md` | وثيقة جرد وبوابة مراحل فقط. |

لا توجد migrations أو تعديلات كود أو تغييرات Docker/Compose ضمن Phase 0.
