# ضوابط الأمن والخصوصية

- JWT verification عبر JWKS وRS256/ES256.
- HMAC أو mTLS للمسارات الداخلية.
- TLS إلزامي.
- Signed URLs قصيرة العمر.
- SHA-256 للتحقق من الملفات.
- Idempotency لكل Job وMaterialization.
- مفاتيح OpenAI في Secret Manager فقط.
- عدم تسجيل Authorization أو API Keys.
- فصل قاعدة AI عن قاعدة المستخدمين.
- أقل قدر من PII داخل خدمة AI.
- Consent مستقل للتدريب.
- حذف وRetention Jobs دوري.
- فحص نوع الملف وحجمه وامتداده في الباك قبل إنشاء Signed URL.
- إضافة Antivirus/OCR Sandbox للملفات غير الموثوقة في الإنتاج.
