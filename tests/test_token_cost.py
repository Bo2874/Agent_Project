from backend.services.token_cost import (
    TokenPricing,
    calculate_cache_hit_rate,
    calculate_token_cost,
)


def test_calculates_cost_by_cache_miss_hit_and_output() -> None:
    cost = calculate_token_cost(
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        cached_input_tokens=250_000,
        cache_miss_input_tokens=750_000,
        pricing=TokenPricing(
            cache_hit_per_million_usd=0.1,
            cache_miss_per_million_usd=1.0,
            output_per_million_usd=2.0,
        ),
    )

    assert cost.cached_input == 0.025
    assert cost.cache_miss_input == 0.75
    assert cost.output == 2.0
    assert cost.estimated_total == 2.775


def test_treats_unclassified_prompt_tokens_as_cache_miss() -> None:
    cost = calculate_token_cost(
        prompt_tokens=100,
        completion_tokens=0,
        cached_input_tokens=20,
        cache_miss_input_tokens=0,
        pricing=TokenPricing(
            cache_hit_per_million_usd=0,
            cache_miss_per_million_usd=1_000_000,
            output_per_million_usd=0,
        ),
    )

    assert cost.cache_miss_input == 80
    assert calculate_cache_hit_rate(20, 80) == 0.2
    assert calculate_cache_hit_rate(0, 0) is None
