"""
Tests for Elo calculation logic (utils/elo.py).
Run: pytest tests/test_elo.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from utils.elo import calculate_elo, calculate_elo_detailed, calculate_win_rate


# ---------------------------------------------------------------------------
# calculate_win_rate
# ---------------------------------------------------------------------------

class TestCalculateWinRate:
    def test_perfect_record(self):
        assert calculate_win_rate(10, 0) == 100.0

    def test_zero_record(self):
        assert calculate_win_rate(0, 10) == 0.0

    def test_no_games(self):
        assert calculate_win_rate(0, 0) == 0.0

    def test_half_record(self):
        assert calculate_win_rate(5, 5) == 50.0

    def test_rounding(self):
        # 1 win / 3 total = 33.33...%
        result = calculate_win_rate(1, 2)
        assert result == 33.33

    def test_large_numbers(self):
        result = calculate_win_rate(999, 1)
        assert result == 99.9


# ---------------------------------------------------------------------------
# calculate_elo - basic properties
# ---------------------------------------------------------------------------

class TestCalculateEloBasic:
    def test_winner_gains_loser_gains_less(self):
        """Winner should always end up higher than starting Elo."""
        new_w, new_l = calculate_elo(500, 500)
        assert new_w > 500

    def test_returns_integers(self):
        new_w, new_l = calculate_elo(500, 500)
        assert isinstance(new_w, int)
        assert isinstance(new_l, int)

    def test_upsets_earn_more(self):
        """Lower-rated winner should gain more than higher-rated winner."""
        # Upset: lower beats higher
        new_upset_w, _ = calculate_elo(400, 600)
        # Expected: higher beats lower
        new_expected_w, _ = calculate_elo(600, 400)
        gain_upset = new_upset_w - 400
        gain_expected = new_expected_w - 600
        assert gain_upset > gain_expected

    def test_equal_elo_default_scores(self):
        """Standard 1v1 at equal Elo with default scores (1-0).

        Formula with bonus_raw = total_games/4 = 1/4 = 0.25:
        raw_gain = 20 * 1 * (1 - 0.5) + 5 = 15, sweep_factor = 1.0 -> actual_gain = 15
        winner_pre_round = 500 + 15 + 0.25 + 0 = 515.25 -> round = 515
        loser_pre_round  = 500 - 15 + 0.25 + 0 = 485.25 -> round = 485
        """
        new_w, new_l = calculate_elo(500, 500)
        assert new_w == 515
        assert new_l == 485


# ---------------------------------------------------------------------------
# calculate_elo - score formats (match-style scoring)
# ---------------------------------------------------------------------------

class TestCalculateEloScores:
    def test_bo5_3_0(self):
        new_w, new_l = calculate_elo(500, 500, winner_score=3, loser_score=0)
        assert new_w > 500

    def test_bo5_3_2(self):
        new_w, new_l = calculate_elo(500, 500, winner_score=3, loser_score=2)
        assert new_w > 500

    def test_bo7_4_0(self):
        new_w, new_l = calculate_elo(500, 500, winner_score=4, loser_score=0)
        assert new_w > 500

    def test_bo7_4_3(self):
        new_w, new_l = calculate_elo(500, 500, winner_score=4, loser_score=3)
        assert new_w > 500

    def test_sweep_gains_more_than_close_score(self):
        """A 3-0 sweep gives more gain than 3-2 at same Elo because the
        higher win quotient (1.0 vs 0.6) and more total_games in 3-2 don't
        overcome the quotient advantage of a sweep."""
        w_close, _ = calculate_elo(500, 500, winner_score=3, loser_score=2)
        w_sweep, _ = calculate_elo(500, 500, winner_score=3, loser_score=0)
        assert (w_sweep - 500) > (w_close - 500)

    def test_bo7_gets_fixed_bonus(self):
        """BO7 winner with score 4 earns the fixed +2 bonus."""
        w_bo7, l_bo7 = calculate_elo(500, 500, winner_score=4, loser_score=3)
        # Verify the BO7 bonus is applied via the detailed breakdown
        detail = calculate_elo_detailed(500, 500, winner_score=4, loser_score=3)
        assert detail["bo7_winner_bonus"] == 2
        assert detail["bo7_loser_bonus"] == -2

    def test_no_bo7_bonus_without_score_4(self):
        """No BO7 bonus when winner score is not 4."""
        detail = calculate_elo_detailed(500, 500, winner_score=3, loser_score=2)
        assert detail["bo7_winner_bonus"] == 0
        assert detail["bo7_loser_bonus"] == 0

    def test_bonus_raw_equals_total_games_over_4(self):
        """The per-set length bonus should equal total_games / 4."""
        detail = calculate_elo_detailed(500, 500, winner_score=3, loser_score=2)
        assert detail["bonus_raw"] == 5 / 4  # total_games = 5


# ---------------------------------------------------------------------------
# calculate_elo - edge cases
# ---------------------------------------------------------------------------

class TestCalculateEloEdgeCases:
    def test_very_high_elo_diff(self):
        """Huge favourite beating underdog: should gain very little."""
        new_w, new_l = calculate_elo(2000, 100, winner_score=3, loser_score=0)
        gain = new_w - 2000
        # Gain should be small
        assert gain <= 10

    def test_elo_cannot_go_negative_from_one_match(self):
        """Single match shouldn't crater a player's Elo to negative."""
        new_w, new_l = calculate_elo(500, 500, winner_score=3, loser_score=0)
        assert new_l >= 0

    def test_zero_scores_raises(self):
        """Both scores zero should raise ValueError (division by zero guard)."""
        with pytest.raises(ValueError, match="total_games cannot be zero"):
            calculate_elo(500, 500, winner_score=0, loser_score=0)


# ---------------------------------------------------------------------------
# calculate_elo_detailed - structure
# ---------------------------------------------------------------------------

class TestCalculateEloDetailed:
    def test_returns_all_expected_keys(self):
        result = calculate_elo_detailed(500, 500, winner_score=3, loser_score=2)
        expected_keys = {
            "total_games", "elo_diff", "expected_lower", "expected_winner",
            "quotient", "raw_gain", "score_gap", "sweep_factor", "actual_gain",
            "bonus_raw", "bo7_winner_bonus", "bo7_loser_bonus",
            "winner_pre_round", "loser_pre_round",
            "new_winner_elo", "new_loser_elo",
        }
        assert set(result.keys()) == expected_keys

    def test_matches_calculate_elo(self):
        """calculate_elo() and calculate_elo_detailed() must produce identical results."""
        for w_elo, l_elo, ws, ls in [
            (500, 500, 3, 2),
            (600, 400, 4, 0),
            (400, 600, 3, 1),
            (800, 200, 4, 3),
        ]:
            simple_w, simple_l = calculate_elo(w_elo, l_elo, winner_score=ws, loser_score=ls)
            detail = calculate_elo_detailed(w_elo, l_elo, winner_score=ws, loser_score=ls)
            assert simple_w == detail["new_winner_elo"]
            assert simple_l == detail["new_loser_elo"]

    def test_expected_winner_adjusts_for_lower_rated(self):
        """When winner has lower Elo, expected_winner should be < 0.5."""
        detail = calculate_elo_detailed(400, 600, winner_score=3, loser_score=2)
        assert detail["expected_winner"] < 0.5

    def test_expected_winner_adjusts_for_higher_rated(self):
        """When winner has higher Elo, expected_winner should be > 0.5."""
        detail = calculate_elo_detailed(600, 400, winner_score=3, loser_score=2)
        assert detail["expected_winner"] > 0.5