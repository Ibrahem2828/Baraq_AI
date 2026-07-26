# البرومبتات وضمان الجودة

لا يوجد Prompt يضمن نتيجة مثالية بنسبة 100%. الجودة المهنية تتحقق عبر طبقات:

1. مصدر موثوق وRAG.
2. System Prompt مقيد بالمهمة.
3. Structured Output Schema.
4. Pydantic Validation.
5. Domain Validator.
6. Source Reference Validation.
7. Backend Rules.
8. User Feedback.
9. Offline Evaluation.
10. مراجعة بشرية للعينات الحساسة.

## إدارة الإصدارات

كل Prompt ملف YAML يضم:

- name
- version
- task_type
- system_prompt
- user_template
- checksum

لا تعدّل Prompt نشطاً بصمت. أنشئ Version جديداً، شغّل Evaluation، ثم انشره تدريجياً.

## مقاومة Prompt Injection

المصادر المرفوعة تعامل كمحتوى غير موثوق. يضيف النظام غلافاً واضحاً حول المقاطع، ويرصد عبارات مثل «تجاهل التعليمات السابقة». لا تُمنح المصادر أي أدوات أو صلاحيات تنفيذ.
