from __future__ import annotations

from prometheus_client import Counter, Histogram

AI_REQUESTS = Counter(
    "baraq_ai_requests_total",
    "AI requests by task and status",
    ["task_type", "status"],
)
AI_PROVIDER_ATTEMPTS = Counter(
    "baraq_ai_provider_attempts_total",
    "Provider attempts by account, model and status",
    ["account", "model", "status"],
)
AI_LATENCY = Histogram(
    "baraq_ai_request_latency_seconds",
    "End-to-end AI request latency",
    ["task_type"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 45, 90, 180, 600),
)
AI_PROVIDER_LATENCY = Histogram(
    "baraq_ai_provider_latency_seconds",
    "Provider call latency",
    ["account", "model"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 45, 90, 180),
)
AI_COST_USD = Counter(
    "baraq_ai_estimated_cost_usd_total",
    "Estimated OpenAI cost in USD",
    ["account", "model"],
)
