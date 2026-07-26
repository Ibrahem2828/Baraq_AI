# تقرير الجاهزية للإنتاج والنشر على Coolify

تاريخ المراجعة: 2026-07-25  
النطاق المراجع: خدمة **Baraq AI Service** الموجودة في هذا المجلد فقط، لا كود Django الموجود خلف `https://api.barraq.xn--mgbaab0cxheq.tech`.

## الحكم التنفيذي

**النتيجة: غير جاهز للرفع إلى Production الآن (Blocked).**

البنية جيدة واحترافية كنقطة بداية: FastAPI غير متزامن، فصل واضح للطبقات، PostgreSQL/pgvector وRedis/Celery، JWT عبر JWKS، HMAC للاتصالات الداخلية، مخرجات منظمة بـPydantic، وعزل معقول للـRAG. لكن توجد أخطاء تشغيلية مؤكدة، أهمها أن migrations لن تعمل من الحزمة كما هي، ومسار تفريغ الصوت `Sada` غير متوافق مع واجهة OpenAI الحالية، وضبط ميزانية التكلفة يعامل كل النماذج كأن تكلفتها صفر. لا يمكن مهنيًا ضمان «عدم وجود أخطاء» قبل إصلاح هذه النقاط وتنفيذ اختبارات تكامل فعلية مع الباك وقاعدة البيانات وOpenAI.

لا يعني ذلك أن المشروع يجب إعادة بنائه؛ بل يحتاج دورة إصلاح وتجهيز Production محددة قبل الإطلاق.

## ما تمت مراجعته وكيف تم التحقق

| العنصر | النتيجة | الملاحظة |
|---|---|---|
| كود Python | 98 ملفًا / 5,232 سطرًا | تمت مراجعة الطبقات وواجهات الربط والإعدادات والمسارات الأساسية. |
| الترجمة النحوية | ناجح | `compileall` نجح لكل `app` و`alembic` و`scripts` و`tests`. |
| فحص الحزمة الداخلي | ناجح | `python scripts/validate_package.py` مرّ، ولم يجد مفاتيح OpenAI بالنمط الذي يفحصه. |
| الاختبارات | غير مكتملة | يوجد 6 ملفات اختبار فقط (150 سطرًا). لم تُشغّل هنا لأن المكتبات غير موجودة، ومحاولة تثبيتها في بيئة معزولة لم تكتمل بسبب مهلة مصدر الحزم. |
| Ruff وMypy | غير منفذين | غير مثبتين في بيئة المراجعة الحالية. |
| Docker Compose | صالح نحويًا | `docker compose config --no-interpolate` نجح، لكن Docker daemon المحلي غير متاح؛ لم يُبنَ أو يُشغّل أي container. |
| OpenAPI | متسق مبدئيًا | الملف يعلن 12 مسارًا، وهي تطابق المسارات المعروضة في الراوترات. |
| فحص المواقع الحية | غير حاسم | لم يمكن التحقق من محتوى نطاقي `api` و`ai` من بيئة المراجعة؛ لا يُستنتج من ذلك أن الخدمة الحية سليمة أو معطلة. |

الوثيقة السابقة `docs/VALIDATION_REPORT_AR.md` تشير إلى «11 passed»، لكن هذه النتيجة **لم تُعدّ مستقلة** في هذه المراجعة؛ لا ينبغي استعمالها كتأكيد للإنتاج قبل إعادة تشغيل الاختبارات في بيئة الحاويات.

## نقاط القوة

- التقسيم منطقي: API، services، pipelines، providers، RAG، models، workers، prompts، وtraining منفصلة.
- مخرجات الشخصيات الخمس تقيَّد بـJSON Schema وPydantic ثم بفحوص أعمال إضافية، وهذا أفضل بكثير من الاعتماد على prompt فقط.
- هناك عزل بين هوية المستخدم في JWT وبين استدعاءات الخدمة الداخلية الموقعة HMAC، مع فحص ملكية الـoutput في feedback.
- الـRAG يتضمن تجزئة عربية، embeddings، pgvector، reranking، citations، وغلافًا للمصادر غير الموثوقة لتقليل prompt injection.
- الحاوية تعمل بمستخدم غير root، و`OPENAI_STORE_RESPONSES=false` افتراضيًا، وملف `.env` مستثنى من Git.
- توجد آلية idempotency، circuit breaker، failover بين حسابين، health endpoints، logging منظم، Prometheus وSentry اختياريان.

## العوائق الحرجة قبل الإطلاق

| الأولوية | المشكلة والدليل | الأثر | الإجراء المطلوب |
|---|---|---|---|
| P0 | `DATABASE_SYNC_URL` يستخدم `postgresql+psycopg` في `app/core/config.py` وAlembic، لكن `psycopg` غير موجود في `pyproject.toml`. | `alembic upgrade head` سيفشل في image المنشور، فلا تُنشأ الجداول أو فهرس pgvector. | إضافة `psycopg[binary]` بإصدار مقيد، ثم بناء image وتشغيل migration على قاعدة فارغة. |
| P0 | `app/providers/openai_provider.py` يرسل `response_format="verbose_json"` لنموذج `gpt-4o-transcribe`. | واجهة OpenAI الحالية تقبل لـ`gpt-4o-transcribe` فقط `json` أو `text`؛ بالتالي تفريغ صدى العادي سيفشل. | اختيار `json`/`text` للمسار العادي وتعديل parser تبعًا لذلك. |
| P0 | عند `diarize=true` يتم اختيار `gpt-4o-transcribe-diarize` ولكن ما زالت الصيغة `verbose_json` ولا يمرر `chunking_strategy`. | المسار غير متوافق؛ diarization يحتاج `diarized_json`، ويحتاج `chunking_strategy="auto"` عندما يتجاوز الملف 30 ثانية. | تنفيذ مسار diarization مستقل بالمعاملات الصحيحة، ثم اختباره بملف قصير وطويل. |
| P0 | `MAX_AUDIO_FILE_BYTES=200MB` بينما حد Upload الرسمي للتفريغ الصوتي هو 25MB؛ لا يوجد split/chunking للملف. | الطلبات بين 25 و200MB ستفشل حتمًا لدى المزوّد، رغم قبول التطبيق لها. | خفض الحد إلى 25MB فورًا أو تنفيذ splitting آمن مع تجميع transcript، وحدد سياسة واضحة لمدة الصوت. |
| P0 | `config/pricing.yaml` يحوي أسعارًا صفرية لكل النماذج، و`transcribe()` يعيد تكلفة صفرية. | ميزانية الحسابين الشهرية وعدادات التكلفة لا تحميان من الإنفاق؛ يمكن تجاوز السقف فعليًا. | إدخال أسعار دقيقة من صفحة التسعير/الحساب، احتساب الصوت، وإضافة تنبيه/حد إنفاق من OpenAI نفسه. |
| P0 | لا توجد migration أو prompt-sync تلقائية في Compose؛ ودليل التشغيل يطلب `scripts/sync_prompts.py` بينما `Dockerfile` لا ينسخ مجلد `scripts/` إلى image. | إجراءات الإطلاق الموثقة لا يمكن تنفيذها داخل الحاوية المنشورة، وقد تبدأ الخدمة قبل المخطط. | نسخ scripts اللازمة أو إنشاء image إداري منفصل، وإضافة migration job واحد يعمل قبل `ai-api` وworker. |
| P0 | ملف Compose ينشر `8001:8000` علنًا، فيما النشر المطلوب عبر Coolify/Traefik. | يمكن تجاوز TLS/proxy والسياسات، كما يصبح تزوير forwarded headers أخطر مع `--forwarded-allow-ips '*'`. | حذف `ports` في Production وربط domain بـCoolify فقط؛ قصر الثقة في forwarded headers على proxy موثوق. |
| P0 | لا يوجد دليل حي بأن الباك يملك JWKS والمسارات الداخلية المتعاقد عليها أو يتحقق من HMAC/idempotency. | حتى مع سلامة خدمة AI لن ينجح reserve/materialize/source-manifest بدون هذا العقد. | تنفيذ Contract/E2E test ضد staging قبل أي تحويل مرور. |

### التوافق الرسمي مع OpenAI

استخدام Responses API و`text.format` مع `json_schema` في التوليد المنظم صحيح من حيث المبدأ. التوثيق الرسمي يصف الصيغة ذاتها ويؤكد دعم المخرجات المنظمة. المشكلة محصورة في Audio API كما في العوائق السابقة. مراجع رسمية: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)، [Speech to Text](https://developers.openai.com/api/docs/guides/speech-to-text)، [gpt-4o-transcribe-diarize](https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize).

النماذج النصية المضبوطة (`gpt-5.1` و`gpt-5-mini` و`gpt-5-nano`) ما زالت موجودة وتدعم Responses وStructured Outputs، لكن الاعتماد على aliases غير مثبتة يجعل السلوك والسعر قابلين للتغير. ثبّت snapshot بعد نجاح evals أو أعد تقييم routing دوريًا. لا تغيّر النماذج لمجرد أنها ليست الأحدث؛ يجب أن يسبق ذلك benchmark لجودة العربية والكلفة. راجع [كتالوج النماذج](https://developers.openai.com/api/docs/models) و[دليل اختيار/ترقية النماذج](https://developers.openai.com/api/docs/guides/latest-model).

## مشاكل عالية الأهمية

1. **الاحتفاظ بالبيانات لا يطابق الإعدادات.** `RAW_CONTENT_RETENTION_DAYS` و`MAX_REQUEST_TEXT_CHARS` و`MAX_AUDIO_SECONDS` و`OPENAI_MODERATION_MODEL` و`OTEL_EXPORTER_OTLP_ENDPOINT` معرفة في الإعدادات ولا يوجد استعمال فعلي لها. عامل التنظيف يحذف jobs القديمة فقط؛ `ai_source_documents` وchunks ذات النص الخام تبقى. هذا خطر خصوصية وتكلفة يجب حسمه بسياسة حذف فعلية وjob مجدول.

2. **مسار النطاق سيعطي 400 على Coolify إن لم يعدّل.** `.env.example` يضع `api.barraq...` في `ALLOWED_HOSTS` ولا يضع `ai.barraq...`. بما أن `TrustedHostMiddleware` مفعّل، يجب إضافة النطاقين إذا كان الوصول عبرهما ممكنًا.

3. **أسرار قاعدة البيانات ضعيفة افتراضيًا.** `POSTGRES_PASSWORD=change_me` موجود حرفيًا في `docker-compose.yml`، و`DATABASE_URL` الافتراضي يحتويه أيضًا. فحص Production يتحقق من HMAC وOpenAI key فقط، لا من كلمة مرور قاعدة البيانات أو Redis. يجب استبدالها بمتغيرات Coolify إلزامية وعدم ترك قيم افتراضية صالحة للعمل.

4. **`/metrics` علني متى كان `PROMETHEUS_ENABLED=true`.** لا يتطلب JWT أو network restriction. قيّد المسار على شبكة المراقبة أو proxy auth/IP allowlist، أو عطله حتى يجهز Prometheus.

5. **عدم ذرية دورة الرصيد والjob.** يجري `reserve_credits` عن بُعد قبل حفظ job وإرساله إلى Celery. فشل commit أو `delay()` يمكن أن يترك reservation محجوزًا بلا job. كذلك `commit/materialize/refund` لا تملك retry/outbox دائمًا في خدمة AI. يجب إضافة outbox/Saga أو reconciliation job، مع idempotency حقيقية في Django لكل reservation وoutput.

6. **الثقة في Source Manifest أوسع من المطلوب.** حقل `owner_user_id` يصل من الباك لكن لا تتم مقارنته مع `job.user_id`. يجب التحقق دفاعيًا، وحصر `download_url` بمضيفي object storage المسموحين ومنع redirects إلى شبكات داخلية إن كان ذلك مناسبًا للبنية.

7. **الترحيلات غير ثابتة زمنيًا.** migration `0001_initial.py` يستدعي `Base.metadata.create_all()` من نماذج اليوم بدل تعريف المخطط التاريخي صراحة. أي تعديل مستقبلي للنماذج قد يجعل تثبيت قاعدة جديدة بإصدار قديم غير قابل لإعادة الإنتاج. أنشئ migrations صريحة لكل تعديل ولا تستخدم `create_all` كـmigration مستدام.

8. **غياب lockfile/constraints.** الحدود العلوية واسعة (`openai<3`, `fastapi<1`…) ولا يوجد lockfile؛ قد ينتج Coolify image مختلفًا في يوم آخر. استخدم lock/constraints موثوقًا وحدّثه في CI.

## مشاكل متوسطة وتحسينات مهنية

- `/health/ready` يعيد HTTP 200 حتى عندما تكون PostgreSQL أو Redis معطلة، ويضع `status=degraded` فقط في JSON. اجعله 503 عند عدم الجاهزية كي يفهمه proxy والمراقبة.
- عقد API يقول إن `Idempotency-Key` header مطلوب، لكن `POST /jobs` يستخدم `idempotency_key` في body، بينما مسارات الشخصيات تستخدم header. وحّد العقد أو صحّح التوثيق وOpenAPI.
- أخطاء Pydantic/HTTPException الافتراضية ليست ضمن `APIEnvelope`، رغم أن الوثائق تعد باستجابة موحدة. أضف handlers لـRequestValidationError وHTTPException.
- `decode_access_token()` يستدعي `PyJWKClient` المتزامن داخل request async؛ عند تحديث JWKS قد يحجب event loop. استخدم عميلًا async/cache مناسبًا أو نفّذه خارج الحلقة.
- feedback يمكن أن ينشئ TrainingDatasetCandidate جديدًا كل مرة يُحدَّث فيها feedback، ولا يوجد قيد فريد يمنع التكرار. أضف unique constraint/upsert.
- ingestion ليس محميًا بقفل/unique-race؛ طلبان لنفس المصدر والإصدار قد يتصادمان عند الإنشاء أو استبدال chunks. استخدم قفل PostgreSQL/Redis أو عالج `IntegrityError` وأعد الجلب.
- تنزيل ملفات 50MB وصوت 200MB في الذاكرة دفعة واحدة مع `concurrency=2` يرفع استهلاك RAM؛ بعد إصلاح حد الصوت استخدم streaming/temp volume أو اضبط limits صريحة للـcontainers.
- الـprompt guard جيد كطبقة أولى، لكنه لا يجعل source آمنًا تمامًا؛ أضف evals حمراء (prompt-injection, data exfiltration) ومراجعة بشرية لعينة الإنتاج.
- `deploy/nginx_baraq_ai.conf` لا يصلح كما هو تلقائيًا مع Coolify: أسماء upstream مفترضة (`baraq-ai-api`, `baraq-backend`) وقد لا تكون على شبكة Docker نفسها. استخدم DNS/شبكة موثقة أو proxy إلى نطاق `ai`.
- مجلد العمل لا يحتوي metadata لـGit؛ لا يمكن إثبات clean worktree أو version قابل للاسترجاع. إن كان النشر من Git، أنشئ مستودعًا وtag للإصدار قبل ربطه بـCoolify.

## البنية الموصى بها للنطاقين والربط

التوصية: اجعل `api.barraq.xn--mgbaab0cxheq.tech` هو **العقد العام الثابت لتطبيق الجوال**، وانشر خدمة AI في Coolify على `ai.barraq.xn--mgbaab0cxheq.tech` كخدمة مستقلة. لا يتصل تطبيق الجوال مباشرة بقاعدة AI أو Redis أو OpenAI.

```text
Mobile / Web client
  │  Bearer JWT + Idempotency-Key
  ▼
https://api.barraq.xn--mgbaab0cxheq.tech/api/ai/v1/*
  │  reverse proxy (قاعدة AI قبل /api/ العامة)
  ▼
https://ai.barraq.xn--mgbaab0cxheq.tech:8000/api/ai/v1/*
  │  JWT عبر JWKS
  ├── PostgreSQL + pgvector (خاصان بخدمة AI)
  ├── Redis + Celery worker/beat (شبكة خاصة)
  ├── OpenAI API
  └── HTTPS + HMAC ──► api.barraq.../api/internal/ai/v1/* (Django)
```

### لماذا هذا الترتيب

- يظل رابط العميل موحدًا كما توثقه الحزمة: `https://api.barraq.xn--mgbaab0cxheq.tech/api/ai/v1`.
- تستخدم خدمة AI الرابط الخارجي للباك فقط في `BARAQ_BACKEND_BASE_URL`؛ لا يلزم أن يساوي نطاق AI.
- نطاق `ai` يبقى مفيدًا لـCoolify والصحة والإدارة/التشخيص، لكنه ليس مصدر truth لعقد الجوال.
- إذا كان Django وAI في نفس Coolify Compose stack، يمكن للباك proxy إلى `http://ai-api:8000` بدل المرور عبر الإنترنت. إن كانا في مشروعين/خادمين مختلفين، proxy إلى `https://ai...` مع TLS والتحقق من الصحة.

### إعداد الـreverse proxy عند الباك

ضع location الخاص بالـAI قبل `location /api/` العام، مع المحافظة على المسار الأصلي:

```nginx
location /api/ai/v1/ {
    proxy_pass https://ai.barraq.xn--mgbaab0cxheq.tech;
    proxy_set_header Host ai.barraq.xn--mgbaab0cxheq.tech;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Request-ID $request_id;
    proxy_read_timeout 360s;
    proxy_send_timeout 360s;
}
```

هذه المهلات تخص إنشاء job السريع لا معالجة OpenAI نفسها، لكنها تمنع قطع الطلب عند بطء الاتصال. لا تعرّض endpoint داخليًا للعامة؛ `api/internal/ai/v1/*` يجب أن يبقى موجّهًا إلى Django فقط ويقبل توقيع HMAC صحيحًا.

## عقد Django الذي يجب إثباته قبل التحويل

الملف المرجعي داخل هذه الخدمة هو `app/core/endpoints.py`. يجب أن يوفر الباك بالضبط:

```text
GET  /.well-known/jwks.json
GET  /api/internal/ai/v1/sources/{source_id}/manifest/?user_id={user_id}
GET  /api/internal/ai/v1/users/{user_id}/context/
POST /api/internal/ai/v1/credits/reserve/
POST /api/internal/ai/v1/credits/commit/
POST /api/internal/ai/v1/credits/refund/
POST /api/internal/ai/v1/materialize/
```

المتطلبات غير القابلة للتنازل:

1. JWT يملك `sub`, `role`, `iss=https://api.barraq.xn--mgbaab0cxheq.tech`, و`aud=baraq-api` وموقع بـRS256 أو ES256؛ لا تستخدم secret مشتركًا لتوقيع JWT.
2. الباك يتحقق من `X-Baraq-Service`, `X-Baraq-Timestamp`, `X-Baraq-Signature`, و`X-Content-SHA256` بتوقيت محدود، ويرفض replay والتوقيعات الخاطئة.
3. source manifest يعيد URL موقّع قصير العمر، ويفحص أن `source_id` ملك فعلًا للمستخدم المرسل، وأن SHA-256 والحجم للملف النهائي.
4. reserve/commit/refund عمليات ذرية وidempotent، وreservation له مهلة انتهاء أو reconciliation.
5. materialize idempotent على `output_id` ويعيد نفس المورد عند التكرار، ولا ينشر Quiz/Summary تلقائيًا قبل quality policy.

لا يمكن الجزم بأن هذه المتطلبات موجودة لأن كود Django ليس ضمن هذا المجلد.

## إعداد Coolify المقترح

استخدم **Docker Compose Build Pack** لأن النظام يتكون من API وworker وbeat وPostgreSQL وRedis. تدعم Coolify Compose كمرجع وحيد للتشغيل، وتكتب ملف `.env` في وقت التشغيل للمتغيرات Runtime، كما أن تعيين domain مع `:8000` يخبر proxy بالمنفذ الداخلي. لا تستخدم `ports` العامة مع هذه البنية. مراجع: [Docker Compose في Coolify](https://coolify.io/docs/knowledge-base/docker/compose)، [متغيرات البيئة](https://coolify.io/docs/knowledge-base/environment-variables)، [Domains](https://coolify.io/docs/knowledge-base/domains)، [Health checks](https://coolify.io/docs/knowledge-base/health-checks).

### متغيرات Production في Coolify

اجعلها Runtime-only وSecrets حيث يلزم، ولا تدخل قيمة سر في Git أو build args:

```env
APP_ENV=production
DEBUG=false
ALLOWED_HOSTS=ai.barraq.xn--mgbaab0cxheq.tech,api.barraq.xn--mgbaab0cxheq.tech

DATABASE_URL=postgresql+asyncpg://...@postgres:5432/baraq_ai
DATABASE_SYNC_URL=postgresql+psycopg://...@postgres:5432/baraq_ai
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

BARAQ_BACKEND_BASE_URL=https://api.barraq.xn--mgbaab0cxheq.tech
BARAQ_BACKEND_JWKS_URL=https://api.barraq.xn--mgbaab0cxheq.tech/.well-known/jwks.json
BARAQ_BACKEND_ISSUER=https://api.barraq.xn--mgbaab0cxheq.tech
BARAQ_BACKEND_AUDIENCE=baraq-api
BARAQ_SERVICE_HMAC_SECRET=<secret-64-random-bytes-or-more>

OPENAI_PRIMARY_API_KEY=<secret>
OPENAI_PRIMARY_PROJECT_ID=<project-id>
OPENAI_PRIMARY_MONTHLY_BUDGET_USD=<verified-limit>
OPENAI_SECONDARY_ENABLED=false
OPENAI_STORE_RESPONSES=false
PROMETHEUS_ENABLED=false
```

أضف `CORS_ORIGINS` فقط إذا كان متصفح يتصل مباشرة بخدمة AI، وبقائمة origins دقيقة. تطبيق الجوال الأصلي لا يحتاج CORS عادة. استخدم كلمة مرور PostgreSQL مولدة عشوائيًا مع URL-encoding في connection strings، واجعل `POSTGRES_PASSWORD` متغيرًا إلزاميًا لا قيمة ثابتة.

### تسلسل النشر بعد الإصلاح

1. أنشئ Git repository خاصًا وcommit قابلًا للإرجاع وtag للإصدار.
2. أصلح كل P0، ثم شغّل `pip install -e '.[dev]'` و`pytest` و`ruff check .` و`mypy app` محليًا/في CI.
3. ابنِ Docker image وجرّب تشغيله مع PostgreSQL 16 + pgvector وRedis في staging.
4. شغّل `alembic upgrade head` كـmigration job واحد، وتحقق أن extension `vector` وفهرس HNSW أنشئا.
5. شغّل مزامنة prompts من image يحتوي السكربت، وتحقق من checksum والإصدارات.
6. في Coolify: عيّن domain API للخدمة `ai-api` إلى `https://ai.barraq.xn--mgbaab0cxheq.tech:8000`، ولا تعيّن domain للـworker أو beat أو قواعد البيانات، واحذف `ports: 8001:8000` للإنتاج.
7. اضبط healthcheck للحاوية على `/api/ai/v1/health/live` ثم راقب `/ready` خارجيًا بعد تغييرها لتعيد 503 عند العطل. لا تعتبر container صحيًا قبل migration.
8. اختبر عقد Django وOpenAI بمفاتيح staging: reserve ثم job ثم materialize ثم commit، وحالة failure ثم refund، ثم إعادة نفس Idempotency-Key.
9. نفذ canary بميزة feature flag (مثل 5–10% من المستخدمين)، راقب errors والتكلفة وlatency، ثم زد النسبة تدريجيًا مع rollback محدد.

## مصفوفة الاختبارات اللازمة قبل الموافقة

| الاختبار | معيار النجاح |
|---|---|
| Migration من قاعدة فارغة | `alembic upgrade head` ينجح وvector/HNSW والجداول تظهر. |
| API health | live=200، ready=200 فقط عندما Redis وPostgreSQL سليمين؛ وإلا 503. |
| JWT/JWKS | token صحيح ينجح، issuer/audience/expiry/role خاطئة ترفض 401/403. |
| HMAC | توقيع صحيح ينجح، body أو path أو timestamp أو replay خاطئ يرفض. |
| Credits | duplicate requests لا تخصم مرتين، والفشل/الإلغاء يعيد الرصيد مرة واحدة. |
| الشخصيات | Fahes/Khota/Rasheed/Kholasa/Sada كلها تنجح بمصادر عربية واقعية وتُنتج schema صالحًا. |
| Sada | ملف أقل من 25MB عادي، diarized قصير وطويل، وملف أكبر يجزأ أو يرفض برسالة واضحة. |
| RAG | PDF/DOCX/PPTX/TXT، مصدر ممسوك لمستخدم آخر، وprompt-injection داخل ملف. |
| ضغط | worker concurrency وRAM وحدود Redis وtimeout وfailover بين حسابين. |
| استعادة | backup/restore لـPostgreSQL volume واختبار rollback للإصدار. |

## القرار النهائي

**لا ترفع النسخة الحالية كإطلاق Production.** يمكن رفعها إلى بيئة staging فقط بعد حل الاعتماد `psycopg`، وإصلاح `Sada` وحد 25MB، وتعبئة الأسعار، وتجهيز migration/prompt-sync. بعد نجاح مصفوفة الاختبارات وربط Django الفعلي، تصبح البنية مناسبة لإطلاق تدريجي مراقب؛ أما وصفها بأنها «مثالية وخالية من الأخطاء» الآن فسيكون غير دقيق تقنيًا.
