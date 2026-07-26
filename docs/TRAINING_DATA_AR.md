# تخزين البيانات والتدريب المستقبلي

## البيانات المسجلة

- Job ومعرف المستخدم الخارجي.
- Task Type.
- Prompt Name/Version.
- Provider Account/Model.
- Token Usage/Cost/Latency.
- Structured Output.
- Citations وGroundedness.
- Feedback الصريح والإشارات الضمنية.

## ما لا يُستخدم تلقائياً للتدريب

- نتيجة حصلت على Dislike من دون تصحيح.
- بيانات بلا Consent.
- بيانات تحتوي PII غير منقحة.
- مخرجات فاشلة في Schema أو Grounding.
- أمثلة مكررة أو منخفضة الجودة.

## المسار الصحيح

```text
Feedback -> Consent -> Anonymization -> Candidate
-> Human Review -> Approved -> Dataset Version
-> Offline Evaluation -> Fine-tuning -> Shadow -> Canary
```

## RAG مقابل Fine-tuning

- RAG: يزود النموذج بمحتوى ملف الطالب الحالي.
- Fine-tuning: يحسن الأسلوب، العربية، مستوى الصعوبة والالتزام بالبنية.
- Rules: تحسب الدرجات والأولوية والوقت والرصيد.
