> **Superseded** — see [`PRODUCTION_RELEASE_STATUS.md`](../PRODUCTION_RELEASE_STATUS.md) at the repo root for the current, verified production status (2026-09-13). Kept below for historical record only.

# BARAQ AI STANDALONE RESULT: FAIL

> **SUPERSEDED / HISTORICAL RECORD — 2026-08-21.** This report describes a Lab
> built directly around DeepSeek, which the binding
> `Baraq_AI_Production_Acceptance_Spec_v1.0` names a Hard Fail (no approved
> provider besides Gemini/OpenAI, with Mock/Replay for offline testing). All
> DeepSeek code was removed on 2026-08-21; the Lab is now provider-neutral via
> `PROVIDER_MODE` (see `docs/AI_LAB.md`). This file is preserved only as a
> historical record of what the standalone Lab looked like before that change
> and must not be read as current guidance.

تاريخ التقرير: 2026-08-20. النتيجة `FAIL` لا تعني قبولاً ناقصاً: بوابة القبول
المحددة تتطلب مفتاح DeepSeek صالحاً، فحص `/models` حي، وتنفيذ الشخصيات الخمس
فعلياً مع صوت حقيقي لـ Local Whisper. فحص حي لاحق وصل إلى التوليد، لكنه توقف
بـ `deepseek_insufficient_balance`؛ لذلك لم يُدَّع نجاح حي غير منفذ.

## 1. Executive Summary

تم بناء Baraq AI Lab مستقلاً داخل هذا المستودع. يعمل بـ FastAPI وJinja2 وHTMX
وSQLite وfilesystem محلي، ويستخدم DeepSeek للتوليد المنظم وLocal Whisper لصدى.
لم يُعدّل Backend أو mobile أو dashboard ولم يُحتج Django/Redis/Celery/PostgreSQL
في وضع Lab.

## 2. Execution Mode

- `BARAQ_RUNTIME_MODE=lab` يركب `/lab` فقط ويهيئ `LabStorage` و`LocalJobManager`.
- لا يستدعي lifespan Redis في Lab، ولا يركب API service/Django gateway.
- `service` يبقى المسار المنفصل المخصص لطبقات الإنتاج المستقبلية.
- يرفض الإعداد `lab` عند `APP_ENV=production`.

## 3. Provider and Model Status

- المزوّد الوحيد للتوليد في Lab هو `DeepSeekProvider`.
- النماذج الافتراضية للطبقات fast/balanced/high-quality هي كلها
  `deepseek-v4-flash`.
- إعداد التكلفة متعقب في `config/pricing.yaml`، ويحسب الإدخال والمخبأ والإخراج.
- لا يوجد fallback صامت إلى OpenAI أو مزود آخر داخل مسار Lab.

## 4. Provider Health and Model Check

- المنفذ: `GET /lab/health` يستدعي DeepSeek `/models` عند وجود مفتاح ويعيد حالة
  الاتصال والنماذج الظاهرة والنماذج المطلوبة من دون سر.
- المنفذ: `scripts/test_deepseek_live.py` يفحص `/models` ثم JSON structured probe.
- النتيجة الفعلية: تجاوز المفتاح وفحص `/models`، ثم أعاد التوليد
  `DEEPSEEK LIVE TEST: FAIL (deepseek_insufficient_balance)`.
- لا يمكن اعتماد التوليد أو latency الحي قبل تفعيل/شحن رصيد الحساب.

## 5. Architecture Layers

| الطبقة | التنفيذ |
| --- | --- |
| UI/API | `app/api/v1/lab.py` وJinja2/HTMX تحت `/lab` |
| Application | `BaraqAIApplication` المشترك بين Lab وadapter خدمة مستقبلي |
| Character logic | schemas وprompt registry وgrounding validators الحالية |
| Provider | `DeepSeekProvider` لتوليد JSON فقط |
| Local retrieval | `LocalLexicalRetriever` BM25-like بلا embedding API |
| Lab adapters | `LabStorage` SQLite/filesystem و`LocalJobManager` |
| STT | `LocalWhisperAdapter` (Faster-Whisper اختياري) |

## 6. End-to-End Data Flow

رفع ملف → تحقق الامتداد/MIME/الحجم → حفظ UUID محلي → استخراج → chunking →
SQLite → اختيار `source_ids` → retrieval مقيد بالـ workspace → evidence context →
DeepSeek structured JSON → Pydantic/grounding validation → job/result/evidence/
metrics محلي → عرض أو export JSON. صدى يسير: audio → Local Whisper raw segments →
DeepSeek cleanup → حفظ raw transcript كما هو → إمكانية حفظ النص المنظف كمصدر.

## 7. Local Storage

- الجذر `.baraq_lab/` والمجلد مستثنى في `.gitignore`.
- SQLite يحتوي workspaces, sources, chunks, jobs, quiz attempts, feedback.
- الملفات تحفظ بأسماء UUID؛ الاسم الأصلي وhash وMIME والحالة وmetadata محفوظة.
- حذف workspace يمسح صفوفه وملفاته المملوكة ضمن مجلد uploads فقط.

## 8. Document Handling

- الأنواع المقبولة: PDF/DOCX/PPTX/TXT/MD وMP3/WAV/M4A.
- PDF النصي: `pypdf` مع page metadata؛ PDF بلا نص يعطي `pdf_ocr_required` ولا
  يدّعي نجاح OCR.
- DOCX: فقرات وعناوين وجداول. PPTX: نص الشرائح والجداول. TXT/MD: decode محلي.
- هناك حد ملف وحد نص مستخرج ورفض MIME غير المتسق مع الامتداد.

## 9. Retrieval

- الاسترجاع معجمي محلي BM25-like، بلا OpenAI embeddings أو API خارجي.
- SQL يربط كل chunk بـ `workspace_id` ويقصره على `source_ids` المحددة.
- evidence يعرض `chunk_id`, `source_id`, hash, page/section, excerpt, lexical score.
- عدم وجود دليل ملائم يوقف النتيجة بـ `insufficient_evidence`.

## 10. Grounding and Evidence Validation

- فاحص وخلاصة يرسلان فقط evidence context إلى prompt.
- الناتج يمر Pydantic، ثم `ClaimEvidenceValidator`، ويحفظ groundedness في
  `validation`.
- الاستشهادات تتضمن IDs UUID حقيقية ولا تُستبدل بمراجع مصطنعة.
- فشل JSON أو schema يعيد محاولة منظمة واحدة، ثم يفشل برسالة آمنة.

## 11. Character-by-Character Status

| الشخصية | التنفيذ الحالي | القبول الحي |
| --- | --- | --- |
| فاحص | retrieval مقيد + quiz JSON + grounding | غير منفذ بلا DeepSeek |
| خلاصة | summary/flashcards JSON + citations | غير منفذ بلا DeepSeek |
| خُطّة | scheduler محلي حتمي، لا DeepSeek للحساب | اختُبر unit بنجاح |
| رشيد | metrics Lab/manual فقط ثم narrative JSON | غير منفذ بلا DeepSeek |
| صدى | Local Whisper ثم cleanup مع حفظ raw transcript | STT المحلي PASS على WAV اصطناعي؛ cleanup محجوب برصيد DeepSeek |

## 12. UI Verification

- الواجهة RTL متاحة على `http://127.0.0.1:8000/lab` في Lab mode.
- تحتوي provider status، upload، source/chunk inspector، اختيار المصادر، نماذج
  افتراضية لكل شخصية، job polling، export JSON، وحفظ transcript source لصدى.
- تحقق فعلي غير يدوي: تشغيل FastAPI الحقيقي من `.venv` أعاد 200 لـ `/lab`
  و`/lab/health` مع `runtime_mode=lab`, `configured=true`, `connected=true`،
  وظهر `deepseek-v4-flash` من `/models`.
- لا يحق ادعاء مرور تدفق المستخدم اليدوي الكامل قبل تشغيل الشخصيات الحية.

## 13. Async Jobs and Background Processing

- `LocalJobManager` ينشئ tasks داخل العملية فقط ويكتب queued/preparing/
  generating|planning/completed|failed في SQLite.
- الواجهة تستطلع status عبر HTMX؛ لا توجد Celery أو Redis أو worker منفصل.
- الأخطاء `AppError` تحفظ code ورسالة آمنة، ولا تحفظ partial accepted output.

## 14. Observability and Cost

- النتيجة تحفظ account/model/response id (إن وجد)/input tokens/output tokens/
  latency/cost وprompt name/version/checksum/thinking flag.
- فحص الصحة لا يطبع المفتاح. أخطاء provider تعطي codes مثل auth/rate limit/
  balance/timeout من دون payload حساس.

## 15. Data Integrity

- deduplication للمصدر داخل workspace عبر SHA-256.
- SQLite foreign keys مفعلة؛ jobs/feedback/attempts مرتبطة بالـ workspace.
- `PlanDay` وschemas الناتج تتحقق من القيود، وtranscript الخام لصدى لا يُستبدل
  بتعديل النموذج.

## 16. Security and Privacy

- لا مفاتيح في Git أو YAML أو UI أو log؛ `.env.example` يحتوي قيمة فارغة فقط.
- واجهة Lab محلية ومقصودة للتطوير، ومحجوبة في production config.
- raw files لا تخرج تلقائياً؛ التوليد اللغوي يرسل مقتطفات evidence اللازمة فقط.
- لا يوجد auth لأن هذا Lab شخصي محلي؛ لا يجوز تعريضه للإنترنت.

## 17. Key Files Changed

- `app/main.py`, `app/api/v1/lab.py`, `app/application/standalone.py`
- `app/lab/{storage,retrieval,stt,jobs}.py`
- `app/providers/deepseek_provider.py`, `app/core/config.py`
- `scripts/run_lab.py`, `scripts/test_deepseek_live.py`,
  `scripts/smoke_lab_characters.py`
- `docs/AI_LAB.md`, `docs/AI_BACKEND_INTEGRATION_READY.md`

## 18. Automated Tests

| الفحص | النتيجة الفعلية |
| --- | --- |
| `python -m pytest` | PASS — 63 passed |
| `python -m mypy app` | PASS — 103 source files |
| Ruff للملفات الجديدة | PASS |
| `python scripts/validate_package.py` | PASS — 134 Python files, 0 errors |
| `tests/unit/test_lab_standalone.py` | PASS — 8 اختبارات تشمل TXT/PDF/DOCX/PPTX/retrieval/Khota/UI/Sada safe failure |
| Local Whisper Tiny + WAV محلي | PASS — مقطع واحد، 5.0 ثوانٍ |
| `smoke_lab_characters.py --audio ...` | وصل إلى التوليد ثم FAIL صحيح: `deepseek_insufficient_balance` |

## 19. Manual Acceptance Tests

غير مكتملة. تحقق تشغيل HTTP وprovider health وLocal Whisper تقنياً نجح، لكن
التوليد الحي غير متاح بسبب رصيد DeepSeek، ولم يُنفذ تدفق الواجهة اليدوي للشخصيات
الخمس. يجب تسجيل نتيجة كل خطوة بعد شحن الرصيد وإعادة smoke.

## 20. Limitations and Blockers

1. blocker: حساب DeepSeek يعيد `deepseek_insufficient_balance`؛ لا يمكن تنفيذ
   JSON generation الحي قبل تفعيل/شحن الرصيد.
2. blocker: لا يوجد بعد اختبار صدى النهائي بالنموذج `small` ومادة عربية حقيقية؛
   محول Whisper نفسه اجتاز تفريغ WAV اصطناعي بالنموذج `tiny`.
3. OCR ليس مشمولاً؛ PDF المصور يفشل صراحةً بدلاً من إنتاج نص غير موثوق.
4. Lab محلي أحادي العملية وليس بديلاً عن queue/authorization production.

## 21. Backend Integration Readiness

`docs/AI_BACKEND_INTEGRATION_READY.md` يحدد adapter boundary والعقود الثابتة.
READY_FOR_BACKEND_INTEGRATION: YES على مستوى schemas/application/grounding؛ لا
يعني نشر production أو اعتماد بوابة Backend قبل مرحلة Service مستقلة.

## 22. Remaining Gaps and Future Work

- شحن/تفعيل رصيد DeepSeek ثم إعادة live probe وfive-character smoke.
- تنفيذ صدى بملف WAV عربي حقيقي وبالنموذج `small` المضبوط.
- مراجعة output examples يدوياً وتشغيل feedback/quiz UX كاملاً.
- إضافة OCR محلي صريح إذا أريد دعم PDF الممسوح.
- بناء adapters production فقط بعد اعتماد backend contracts، لا داخل Lab.

## 23. Final Result and Gate

**BARAQ AI STANDALONE RESULT: FAIL**

الكود والاختبارات المحلية والصحة الحية وLocal Whisper نجحت، لكن شروط PASS
النهائية لم تتحقق: generation حي وfive-character smoke متوقفان على رصيد
DeepSeek، وmanual browser acceptance غير مكتمل. بعد شحن الرصيد وتشغيل
السكربتين أعلاه بنجاح وتوثيق التدفق اليدوي، يمكن تحديث هذه البوابة إلى `PASS`
فقط إذا لم يظهر فشل.

المراجع الرسمية المستخدمة لعقد DeepSeek: [List Models](https://api-docs.deepseek.com/api/list-models)،
[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion)،
[Pricing](https://api-docs.deepseek.com/quick_start/pricing/).
