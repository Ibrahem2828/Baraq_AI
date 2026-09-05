# Baraq AI Lab — التشغيل المحلي والداخلي

هذه البيئة مخصصة للاستخدام الهندسي الداخلي فقط، وهي **provider-neutral** (Gemini
أو OpenAI أو Mock أو Replay خلف نفس الواجهة `LLMProvider`) وليست مبنية حول أي
مزود واحد. لا تُركَّب أبداً عند `APP_ENV=production` (انظر `app/main.py`)، سواء
كعملية مستقلة (`BARAQ_RUNTIME_MODE=lab`) أو كملحق داخلي لخدمة تعمل فعلياً
(`ENABLE_AI_LAB=true` مع `APP_ENV != production`).

## المتطلبات

- Python 3.12 أو أحدث.
- لا حاجة لأي مفتاح مزود: `PROVIDER_MODE=mock` (الافتراضي) يشغّل كل الشخصيات
  الخمس دون شبكة أو تكلفة. `PROVIDER_MODE=replay` يعيد تشغيل ثوابت JSON محفوظة.
  `PROVIDER_MODE=live` يتطلب مفتاح Gemini أو OpenAI حقيقياً.
- لتشغيل صدى: `pip install -e ".[lab]"` لتثبيت Faster-Whisper محلياً. تنزيل
  نموذج Whisper يتم عند أول تشغيل لصدى حسب بيئة Faster-Whisper.

## إعداد البيئة

انسخ `.env.example` إلى `.env`. الإعداد الافتراضي يعمل بلا أي تعديل:

```env
BARAQ_RUNTIME_MODE=lab
PROVIDER_MODE=mock
SADA_STT_PROVIDER=local_whisper
```

لتفعيل مزود حي لاحقاً، غيّر `PROVIDER_MODE=live` واملأ `GEMINI_PRIMARY_*` أو
`OPENAI_PRIMARY_*` في `.env` المحلي فقط -- لا تُدخل أي مفتاح في Git أو الواجهة
أو السجلات. عند `PROVIDER_MODE=live` بلا مفتاح مضبوط، يرجع Lab تلقائياً إلى
Mock مع شارة واضحة في `/lab/health` بدلاً من الانهيار.

## التشغيل

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[lab]"
.\.venv\Scripts\python.exe scripts\run_lab.py
```

استخدم `.venv` بدلاً من Python العام، خصوصاً عند ظهور تحذيرات مثل
`Ignoring invalid distribution` أو أخطاء ملفات `onnxruntime` في `C:\Python312`.

افتح `http://127.0.0.1:8000/lab`. تسع صفحات مستقلة قابلة للوصول مباشرة
(spec section 30):

| المسار | المحتوى |
| --- | --- |
| `/lab/overview` | حالة الـruntime، PROVIDER_MODE، النماذج المضبوطة، دون كشف أي سر. |
| `/lab/sources-rag` | رفع/استخراج مصدر، chunks، الاسترجاع المعجمي. |
| `/lab/prompts` | سجل الأوامر (Prompt Registry): الاسم/الإصدار/checksum. |
| `/lab/fahes`, `/lab/kholasa`, `/lab/khota`, `/lab/rasheed`, `/lab/sada` | تشغيل الشخصية مع JSON قابل للتعديل، مصادر محددة، نتيجة + استشهادات + تحقق. |
| `/lab/evals-providers` | مقارنة تشغيلات حديثة: provider/model/tokens/cost بشارة SIMULATED واضحة في mock/replay. |

## فحص المزوّد الحي

```powershell
.\.venv\Scripts\python.exe scripts\smoke_lab_characters.py --audio C:\path\lecture.wav
```

يعمل هذا السكربت تحت `PROVIDER_MODE` الحالي (mock/replay/live) ويطبع المزود
والحساب الفعليين المستخدمين. عند `live` بمفتاح صحيح، فشل المفتاح أو الرصيد أو
الشبكة أو النموذج يعطي `FAIL` صريحاً ولا يوجد fallback صامت إلى نجاح مصطنع.

يتطلب الاختبار صوتاً حقيقياً لأن صدى يجب أن يختبر Local Whisper فعلياً. يختبر
خلاصة وفاحص وخُطّة ورشيد وصدى، ويتوقف بفشل صريح إذا فشل أي مخطط أو grounding أو
المزوّد أو STT.

## الخصوصية وحدود البيانات

- الملفات الأصلية وchunks والمهام والتغذية الراجعة ومقاييس الاختبار داخل
  `.baraq_lab/` فقط.
- استرجاع فاحص وخلاصة BM25 محلي ويقتصر SQL فيه على `workspace_id` و`source_ids`
  المحددة. تُرسل إلى مزود التوليد المضبوط فقط مقتطفات الأدلة المطلوبة للناتج
  اللغوي.
- رشيد لا يستدعي بيانات مستخدم أو Backend؛ يقبل فقط المقاييس اليدوية أو مقاييس
  جلسة الاختبار المحفوظة محلياً.
- صدى يفرغ الصوت بواسطة Local Whisper، ثم يستخدم مزود التوليد المضبوط لتنظيم
  النص فقط مع حفظ `full_transcript` الخام كما خرج محلياً. يمكن حفظ النص المنظف
  كمصدر TXT.
- لا تستخدم هذه البيئة كخدمة إنترنت أو بيئة إنتاج.

## ملاحظة تاريخية

نسخة سابقة من Lab كانت مبنية مباشرة حول DeepSeek (`app/providers/deepseek_provider.py`
سابقاً). أُزيل ذلك بالكامل في 2026-08-21 امتثالاً لمعيار القبول الذي يحظر أي
مزود غير معتمد؛ Lab الآن provider-neutral عبر `PROVIDER_MODE` كما هو موثق أعلاه.
