# التعديلات المطلوبة على باك برّاق للربط مع خدمة AI المستقلة

## 1. التوجيه الخارجي

اضبط Reverse Proxy بحيث يكون:

```text
/api/ai/v1/* -> baraq-ai-service:8000/api/ai/v1/*
/api/*       -> django-backend:8000/api/*
```

بهذا يبقى المسار العام موحداً:

```text
https://api.barraq.xn--mgbaab0cxheq.tech/api/ai/v1/
```

## 2. JWT غير متماثل وJWKS

يجب أن يوقّع Django Access Tokens باستخدام RS256 أو ES256 بدلاً من مشاركة Secret متماثل مع خدمة AI. أضف:

```text
GET /.well-known/jwks.json
```

Claims المطلوبة:

```json
{
  "sub": "user_id",
  "role": "student",
  "iss": "https://api.barraq.xn--mgbaab0cxheq.tech",
  "aud": "baraq-api",
  "iat": 0,
  "exp": 0,
  "jti": "uuid"
}
```

## 3. مسارات داخلية بين الخدمتين

هذه المسارات لا تكون متاحة للعامة، وتقبل HMAC أو mTLS فقط:

```text
GET  /api/internal/ai/v1/sources/{source_id}/manifest/?user_id={user_id}
GET  /api/internal/ai/v1/users/{user_id}/context/
POST /api/internal/ai/v1/credits/reserve/
POST /api/internal/ai/v1/credits/commit/
POST /api/internal/ai/v1/credits/refund/
POST /api/internal/ai/v1/materialize/
```

المصدر المركزي لهذه المسارات داخل خدمة AI هو:

```text
app/core/endpoints.py
```

## 4. Source Manifest

يجب أن يرجع الباك:

```json
{
  "source_id": "123",
  "owner_user_id": "45",
  "title": "lecture.pdf",
  "mime_type": "application/pdf",
  "size_bytes": 123456,
  "content_sha256": "64-hex",
  "download_url": "https://signed-object-storage-url",
  "download_url_expires_at": "ISO-8601",
  "subject_id": "7",
  "metadata": {}
}
```

الشروط:

- التحقق أن المصدر مملوك للمستخدم.
- Signed URL قصيرة العمر.
- SHA-256 محسوب على الملف النهائي.
- عدم إرسال مسار محلي أو Secret Storage Credential.

## 5. Credit Ledger ذري

يجب أن يدعم الباك دورة:

```text
reserve -> commit
        -> refund
```

ولا يكفي فحص الرصيد ثم الخصم في عمليتين منفصلتين. استخدم معاملة قاعدة بيانات وقفلاً مناسباً، واربط كل Reservation بـIdempotency Key.

## 6. Learner Context

مسار context يعيد بيانات موثوقة فقط:

- المرحلة والصف والتخصص.
- المواد المختارة.
- الوقت اليومي.
- مواعيد الامتحانات.
- إحصاءات الاختبارات والمهام المحسوبة في Django.

لا ترسل Passwords أو Refresh Tokens أو بيانات لا تحتاجها المهمة.

## 7. Materialization

بعد اكتمال Job، ترسل خدمة AI نتيجة منظمة. يقوم Django بـ:

- التحقق من user_id وjob_id وoutput_id.
- منع التكرار باستخدام output_id.
- فاحص: إنشاء Quiz/Question/Choice في حالة Draft.
- خُطى: إنشاء StudyPlan/StudyTask.
- رشيد: إنشاء Recommendation Snapshot.
- خُلاصة: إنشاء Summary محفوظ.
- صدى: إنشاء Transcript وربطه بالمصدر.

يجب عدم نشر اختبار مولد آلياً مباشرة دون Quality Gate أو سياسة المشروع.

## 8. CORS والموبايل

تطبيق الجوال يتصل بنفس النطاق العام، ولا يعرف عنوان الخدمة الداخلي أو مفاتيح OpenAI. جميع طلبات الشخصيات تنتقل إلى `/api/ai/v1`.

## 9. Observability

مرّر الرؤوس:

```text
X-Request-ID
traceparent
```

وسجّل في الباك:

- user_id
- AI job_id
- materialized resource id
- credit reservation id
- status

من دون تسجيل التوكن أو محتوى الملف الكامل.

## 10. خطة انتقال من ai_gateway القديم

1. إبقاء المسارات القديمة مؤقتاً.
2. إضافة Feature Flag لكل شخصية.
3. توجيه Staging إلى الخدمة الجديدة.
4. تشغيل Shadow/Canary.
5. مقارنة الجودة والتكلفة والLatency.
6. تحويل 10% ثم 50% ثم 100%.
7. إزالة Mock القديم بعد فترة استقرار وRollback Window.
