# إعداد حسابي OpenAI

## مكان المفاتيح

المكان الوحيد هو Secret Manager أو `.env` على الخادم:

```env
OPENAI_PRIMARY_API_KEY=
OPENAI_PRIMARY_PROJECT_ID=
OPENAI_SECONDARY_API_KEY=
OPENAI_SECONDARY_PROJECT_ID=
```

## الموديلات

تدار من متغيرات البيئة:

```env
OPENAI_MODEL_HIGH_QUALITY=gpt-5.1
OPENAI_MODEL_BALANCED=gpt-5-mini
OPENAI_MODEL_FAST=gpt-5-nano
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-transcribe
```

يمكن تغييرها دون تعديل الكود. يجب التحقق من توفر الموديل لكل Project باستخدام API models أو لوحة OpenAI قبل الإنتاج.

## Structured Outputs

تستخدم الخدمة Responses API مع `json_schema` و`strict=true`، ثم تعيد التحقق بواسطة Pydantic وقواعد المجال. لا يعتمد النظام على Prompt وحده.

## الاحتفاظ بالبيانات

القيمة الافتراضية:

```env
OPENAI_STORE_RESPONSES=false
```

وتخزن برّاق النتائج اللازمة في قاعدة بياناتها وفق سياسة الخصوصية الخاصة بها. راجع Data Controls في حساب OpenAI عند اختيار المنطقة أو Zero Data Retention.

## الميزانية

```env
OPENAI_PRIMARY_MONTHLY_BUDGET_USD=50
OPENAI_SECONDARY_MONTHLY_BUDGET_USD=50
```

حدث `config/pricing.yaml` بأسعار الحساب الفعلية قبل الاعتماد على تقارير التكلفة.
