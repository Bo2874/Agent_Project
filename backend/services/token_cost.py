from __future__ import annotations

from dataclasses import dataclass

from backend.core.config import get_settings
from backend.schemas.admin import AdminCostMetrics

TOKENS_PER_MILLION = 1_000_000


@dataclass(frozen=True)
class TokenPricing:
    cache_hit_per_million_usd: float
    cache_miss_per_million_usd: float
    output_per_million_usd: float


def configured_token_pricing() -> TokenPricing:
    settings = get_settings()
    return TokenPricing(
        cache_hit_per_million_usd=settings.deepseek_cache_hit_price_per_million_usd,
        cache_miss_per_million_usd=settings.deepseek_cache_miss_price_per_million_usd,
        output_per_million_usd=settings.deepseek_output_price_per_million_usd,
    )


def calculate_token_cost(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_input_tokens: int,
    cache_miss_input_tokens: int,
    pricing: TokenPricing | None = None,
) -> AdminCostMetrics:
    rates = pricing or configured_token_pricing()
    hit_tokens = max(cached_input_tokens, 0)
    reported_miss_tokens = max(cache_miss_input_tokens, 0)
    unclassified_input_tokens = max(prompt_tokens - hit_tokens - reported_miss_tokens, 0)
    miss_tokens = reported_miss_tokens + unclassified_input_tokens

    cached_cost = hit_tokens * rates.cache_hit_per_million_usd / TOKENS_PER_MILLION
    miss_cost = miss_tokens * rates.cache_miss_per_million_usd / TOKENS_PER_MILLION
    output_cost = max(completion_tokens, 0) * rates.output_per_million_usd / TOKENS_PER_MILLION
    return AdminCostMetrics(
        estimated_total=round(cached_cost + miss_cost + output_cost, 8),
        cached_input=round(cached_cost, 8),
        cache_miss_input=round(miss_cost, 8),
        output=round(output_cost, 8),
    )


def calculate_cache_hit_rate(hit_tokens: int, miss_tokens: int) -> float | None:
    total = max(hit_tokens, 0) + max(miss_tokens, 0)
    if total == 0:
        return None
    return max(hit_tokens, 0) / total
