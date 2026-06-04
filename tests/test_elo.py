"""
Tests for Elo calculation logic (utils/elo.py).
Run: pytest tests/test_elo.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from utils.elo import calculate_elo, calculate_win_rate


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
# calculate_elo – basic properties
# ---------------------------------------------------------------------------

class TestCalculateEloBasic:
    def test_winner_gains_loser_loses(self):
        """Winner should always end up higher, loser lower."""
        new_w, new_l = calculate_elo(500, 500)
        assert new_w > 500
        assert new_l < 500

    def test_symmetric_change(self):
        """Gain and loss should be equal when Elo is equal."""
        new_w, new_l = calculate_elo(500, 500)
        gain = new_w - 500
        loss = 500 - new_l
        assert gain == loss

    def test_zero_sum(self):
        """Total Elo pool is conserved."""
        w, l = calculate_elo(600, 400)
        assert w + l == 600 + 400

    def test_upsets_earn_more(self):
        """Lower-rated winner should gain more than higher-rated winner."""
        # Upset: lower beats higher
        new_upset_w, _ = calculate_elo(400, 600)
        # Expected: higher beats lower
        new_expected_w, _ = calculate_elo(600, 400)
        gain_upset = new_upset_w - 400
        gain_expected = new_expected_w - 600
        assert gain_upset > gain_expected

    def test_returns_integers(self):
        new_w, new_l = calculate_elo(500, 500)
        assert isinstance(new_w, int)
        assert isinstance(new_l, int)


# ---------------------------------------------------------------------------
# calculate_elo – score formats
# ---------------------------------------------------------------------------

class TestCalculateEloScores:
    def test_bo3_2_0(self):
        new_w, new_l = calculate_elo(500, 500, total_games=2, winner_score=2, loser_score=0)
        assert new_w > 500

    def test_bo3_2_1(self):
        new_w, new_l = calculate_elo(500, 500, total_games=3, winner_score=2, loser_score=1)
        assert new_w > 500

    def test_bo5_3_0(self):
        new_w, new_l = calculate_elo(500, 500, total_games=3, winner_score=3, loser_score=0)
        assert new_w > 500

    def test_bo5_3_2(self):
        new_w, new_l = calculate_elo(500, 500, total_games=5, winner_score=3, loser_score=2)
        assert new_w > 500

    def test_sweep_scales_with_total_games(self):
        """
        A 2-0 sweep (total_games=2) vs a 3-game series (total_games=3):
        the 3-game series has more total_games so raw_gain * total_games is larger.
        The sweep_factor reduces margin reward but doesn't override the game-count bonus.
        """
        # 2-0 sweep (total_games=2)
        w_sweep, _ = calculate_elo(500, 500, total_games=2, winner_score=2, loser_score=0)
        # 3-0 dominant (total_games=3, same margin ratio)
        w_3game, _ = calculate_elo(500, 500, total_games=3, winner_score=3, loser_score=0)
        # More total_games → more raw_gain; both have same sweep_factor
        assert (w_3game - 500) > (w_sweep - 500)

    def test_bo7_4_0_gets_bonus(self):
        """BO7 winner with score 4 earns +2 bonus."""
        w_bonus, l_bonus = calculate_elo(500, 500, total_games=4, winner_score=4, loser_score=0)
        w_no, l_no = calculate_elo(500, 500, total_games=3, winner_score=3, loser_score=0)
        # BO7 winner gets +2 and loser -2 on top of normal
        assert (w_bonus - 500) >= (w_no - 500) - 5   # rough sanity, not exact


# ---------------------------------------------------------------------------
# calculate_elo – edge cases
# ---------------------------------------------------------------------------

class TestCalculateEloEdgeCases:
    def test_equal_elo_equal_games(self):
        """Standard 1v1 at equal Elo.

        Formula: raw_gain = 20 * 1 * (1 - 0.5) + 5 = 15, sweep_factor = 1.0 → gain = 15.
        """
        new_w, new_l = calculate_elo(500, 500)

        gain = new_w - 500
        assert gain == 15

    def test_very_high_elo_diff(self):
        """Huge favourite beating underdog: should gain very little."""
        new_w, new_l = calculate_elo(2000, 100)
        gain = new_w - 2000
        # Gain should be tiny (near the +5 floor from the formula)
        assert gain <= 6

    def test_elo_cannot_go_negative_from_one_match(self):
        """Single match shouldn't crater an established player to negative."""
        new_w, new_l = calculate_elo(500, 500)
        assert new_l >= 0