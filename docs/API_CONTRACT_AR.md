# عقد API الخارجي لخدمة الذكاء الاصطناعي

## العنوان الأساسي

```text
https://api.barraq.xn--mgbaab0cxheq.tech/api/ai/v1
```

جميع المسارات، باستثناء health، تتطلب:

```http
Authorization: Bearer <access_token>
Idempotency-Key: <unique-client-request-key>
Content-Type: application/json
```

## الاستجابة الموحدة

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": {}
}
```

## إنشاء اختبار فاحص

```http
POST /fahes/quizzes
```

```json
{
  "source_ids": ["123"],
  "subject_id": "7",
  "topic": "الجهاز العصبي",
  "question_count": 10,
  "difficulty": "medium",
  "question_types": ["mcq", "true_false"],
  "language": "ar"
}
```

## إنشاء خطة خُطى

```http
POST /khota/plans
```

```json
{
  "source_ids": ["123"],
  "subject_ids": ["7", "8"],
  "start_date": "2026-08-01",
  "end_date": "2026-08-14",
  "daily_available_minutes": 120,
  "exam_dates": {"7": "2026-08-15"},
  "weak_topics": ["7: التفاضل"],
  "excluded_dates": [],
  "preferred_session_minutes": 45,
  "language": "ar"
}
```

## توصيات رشيد

```http
POST /rasheed/recommendations
```

يجب أن تكون `metrics` و`topic_performance` محسوبة في الباك، وليست نصاً حراً من المستخدم.

## إنشاء ملخص خُلاصة

```http
POST /kholasa/summaries
```

## تفريغ صدى

```http
POST /sada/transcriptions
```

يستقبل `source_id` لملف صوتي محفوظ مسبقاً في الباك. لا تُرسل ملفات كبيرة مباشرة عبر هذا المسار.

## متابعة Job

```http
GET /jobs/{job_id}
```

الحالات:

```text
queued -> processing -> retrieving -> generating -> validating
       -> materializing -> completed
       -> failed | canceled
```

## إلغاء Job

```http
POST /jobs/{job_id}/cancel
```

## Feedback

```http
POST /feedback
```

```json
{
  "output_id": "uuid",
  "rating": 4,
  "is_helpful": true,
  "issue_types": [],
  "comment": "الأسئلة جيدة",
  "corrected_output": null,
  "consent_for_training": false,
  "implicit_signals": {}
}
```
