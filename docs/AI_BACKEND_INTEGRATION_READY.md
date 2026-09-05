# Baraq AI — عقد الجاهزية لدمج الـ Backend لاحقاً

`BaraqAIApplication` في `app/application/standalone.py` هو حد التطبيق المشترك.
لا تعتمد محركات الشخصيات فيه على HTTP أو قالب الواجهة أو Celery؛ تستقبل
`workspace_id` و`TaskType` و`input` وتعيد نتيجة موحدة تحتوي `result` و`evidence`
و`validation` و`provider`.

## ما يبقى ثابتاً عند الانتقال إلى Service mode

| المجال | العقد المستعمل الآن | ما يستبدل لاحقاً |
| --- | --- | --- |
| الشخصيات | `FahesRequest/Result`, `KholasaRequest/Result`, `KhotaRequest/Result`, `RasheedRequest/Result`, `SadaRequest/Result` | لا تغيير في schemas أو prompts أو validators |
| التوليد | `LLMProvider.generate_structured` (Gemini/OpenAI/Mock/Replay خلف `PROVIDER_MODE`) وJSON Pydantic | adapter/credentials/routing فقط -- الواجهة نفسها بالفعل |
| grounding | `ClaimEvidenceValidator` و`Citation` | مستودع/استرجاع production بدلاً من BM25 المحلي |
| التخزين | `LabStorage` (SQLite/filesystem) | repository PostgreSQL/object storage |
| jobs | `LocalJobManager` (in-process) | job repository + queue/worker adapter |
| الصوت | `LocalWhisperAdapter` ثم مزود التوليد المضبوط للتنظيف | STT adapter مضبوط صراحة؛ لا fallback مخفي |

## واجهة الإدخال المقترحة للـ Backend adapter

1. يتحقق Backend من المستخدم والملكية والاشتراك ويجمع `source_ids` المصرح بها.
2. ينشئ adapter طلباً مطابقاً لأحد schemas أعلاه ويستدعي نفس application service.
3. يوفر adapter للأدلة يضمن عزل `user_id/workspace_id` قبل أي retrieval.
4. يحفظ `result/evidence/validation/provider` كما هي في job/output production.
5. ينشر status transitions عبر queue/outbox production؛ لا ينقل SQLite أو
   `LocalJobManager` إلى الإنتاج.

## تعاقدات لا يجوز كسرها

- أسماء المهام: `fahes_generate_quiz`, `khota_generate_plan`,
  `rasheed_recommendations`, `kholasa_generate_summary`, `sada_transcribe_audio`.
- لا يقبل فاحص أو خلاصة ادعاءات بلا دليل؛ فشل evidence هو فشل آمن.
- خُطّة تظل جدولة قواعد/قيود، ورشيد يتطلب مقاييس موثوقة، وصدى يحافظ على النص
  الخام والمقاطع الزمنية.
- لا يمر أي مفتاح مزود (Gemini/OpenAI) إلى Backend أو العميل أو output/telemetry.

READY_FOR_BACKEND_INTEGRATION: YES (على مستوى contracts/adapters؛ يلزم تشغيل
بوابة المصادقة وبيانات الإنتاج واختبارات التكامل في مرحلة Service مستقلة).

## ملاحظة تاريخية

نسخة سابقة من هذه الوثيقة افترضت DeepSeek كمزوّد وحيد لـLab قبل بناء خدمة
Service production منفصلة (`app/services/job_service.py`,
`app/pipelines/*`, RAG على Postgres/pgvector). كلا المسارين موجودان الآن؛
Lab يبقى محلياً وprovider-neutral عبر `PROVIDER_MODE` (`app/lab/providers.py`)،
والخدمة الحقيقية تستخدم `ProviderRouter` (`app/providers/router.py`) بنفس
واجهة `LLMProvider`. أُزيل (removed) DeepSeek بالكامل في 2026-08-21.
