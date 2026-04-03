"""Tests for tier-aware pricing in TokenTracker."""

from __future__ import annotations

from tldr.token_tracker import TokenTracker


FLAT_PRICING = {
    "gemini-2.0-flash": {
        "input_per_1m": 0.075,
        "output_per_1m": 0.30,
    },
}

TIER_PRICING = {
    "gemini-3-flash-preview": {
        "standard": {
            "input_per_1m": 0.50,
            "output_per_1m": 3.00,
        },
        "flex": {
            "input_per_1m": 0.25,
            "output_per_1m": 1.50,
        },
        "priority": {
            "input_per_1m": 1.00,
            "output_per_1m": 6.00,
        },
    },
}

MIXED_PRICING = {
    "gemini-2.0-flash": {
        "input_per_1m": 0.075,
        "output_per_1m": 0.30,
    },
    "gemini-3-flash-preview": {
        "standard": {
            "input_per_1m": 0.50,
            "output_per_1m": 3.00,
        },
        "flex": {
            "input_per_1m": 0.25,
            "output_per_1m": 1.50,
        },
    },
}


class TestTierAwarePricing:
    """Tier-based pricing resolution in TokenTracker."""

    def test_flat_pricing_unchanged_without_tier(self):
        """Flat pricing is used as-is when no service_tier is set."""
        tracker = TokenTracker(pricing=FLAT_PRICING)
        tracker.record("gemini-2.0-flash", input_tokens=1_000_000, output_tokens=0)

        assert abs(tracker.total_cost() - 0.075) < 1e-6

    def test_tier_selects_flex_pricing(self):
        """When service_tier='flex', flex rates are used."""
        tracker = TokenTracker(pricing=TIER_PRICING, service_tier="flex")
        tracker.record("gemini-3-flash-preview", input_tokens=1_000_000, output_tokens=0)

        assert abs(tracker.total_cost() - 0.25) < 1e-6

    def test_tier_selects_priority_pricing(self):
        """When service_tier='priority', priority rates are used."""
        tracker = TokenTracker(pricing=TIER_PRICING, service_tier="priority")
        tracker.record("gemini-3-flash-preview", input_tokens=1_000_000, output_tokens=0)

        assert abs(tracker.total_cost() - 1.00) < 1e-6

    def test_no_tier_defaults_to_standard(self):
        """When no service_tier is set, standard rates are used for tier-aware models."""
        tracker = TokenTracker(pricing=TIER_PRICING)
        tracker.record("gemini-3-flash-preview", input_tokens=1_000_000, output_tokens=0)

        assert abs(tracker.total_cost() - 0.50) < 1e-6

    def test_missing_tier_falls_back_to_standard(self):
        """When a requested tier doesn't exist, standard is used."""
        tracker = TokenTracker(pricing=TIER_PRICING, service_tier="nonexistent")
        tracker.record("gemini-3-flash-preview", input_tokens=1_000_000, output_tokens=0)

        assert abs(tracker.total_cost() - 0.50) < 1e-6

    def test_mixed_flat_and_tier_pricing(self):
        """Flat and tier-aware entries coexist in the same pricing table."""
        tracker = TokenTracker(pricing=MIXED_PRICING, service_tier="flex")

        tracker.record("gemini-2.0-flash", input_tokens=1_000_000, output_tokens=0)
        tracker.record("gemini-3-flash-preview", input_tokens=1_000_000, output_tokens=0)

        # 0.075 (flat) + 0.25 (flex tier)
        assert abs(tracker.total_cost() - 0.325) < 1e-6

    def test_live_line_includes_cost(self):
        """live_line() should include the cost string when pricing is set."""
        tracker = TokenTracker(pricing=FLAT_PRICING)
        tracker.record("gemini-2.0-flash", input_tokens=1000, output_tokens=500)

        line = tracker.live_line()
        assert "1,000 in" in line
        assert "500 out" in line
        assert "$" in line

    def test_summary_with_tier_pricing(self):
        """summary() produces output with tier-resolved pricing."""
        tracker = TokenTracker(pricing=TIER_PRICING, service_tier="flex")
        tracker.record("gemini-3-flash-preview", input_tokens=1000, output_tokens=500)

        summary = tracker.summary()
        assert "gemini-3-flash-preview" in summary
        assert "$" in summary
