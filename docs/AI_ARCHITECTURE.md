# Baraq AI architecture

Django is the public gateway and owns users, sources, authorisation and
materialisation. Baraq AI accepts only HMAC V2 service requests, stores the job
and its dispatch outbox row atomically, and runs the five pipelines through
Celery. PostgreSQL holds job, output, provider-attempt and durable outbox
state; Redis provides replay prevention, Celery transport and circuit state.

Completion is two-stage: a worker commits `AIOutput`, moves the job to
`completed`, and inserts `deliver_result_webhook` in the same transaction. A
separate handler signs the stable event with HMAC V2 and retries only that
webhook on Django failure. This prevents a lost callback from regenerating an
answer.

Every job persists `request_id`, frozen source hashes, prompt checksum,
pipeline version and fallback policy. No complete source text is emitted to
structured logs.
