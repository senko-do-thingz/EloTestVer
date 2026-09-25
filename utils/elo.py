"""Elo calculation logic.

Single source of truth for the Elo formula used at match commit time.
Every caller (reporting, admin report, devmode preview, tests) must use
the functions defined here — no separate formula implementations should
exist elsewhere in the codebase.
"""

import math


# ---------------------------------------------------------------------------
# Formula constants
# ---------------------------------------------------------------------------

EXPECTED_OUTCOME_DIVISOR = 1147.44429731658
RAW_GAIN_MULTIPLIER = 20
SET_WIN_BONUS = 5
SWEEP_REDUCTION_FACTOR = 0.076923
BO7_FIXED_BONUS = 2


# ---------------------------------------------------------------------------
# Win rate
# ---------------------------------------------------------------------------

def calculate_win_rate(games_won: int, games_lost: int) -> float:
    total_games = games_won + games_lost
    return round((games_won / total_games) * 100, 2) if total_games > 0 else 0.0


# ---------------------------------------------------------------------------
# Elo calculation
# ---------------------------------------------------------------------------

def calculate_elo(
    winner_elo: int,
    loser_elo: int,
    winner_score: int = 1,
    loser_score: int = 0,
) -> tuple[int, int]:
    """Calculate new Elo ratings after a match.

    This is the canonical formula used at commit time. It includes:
    - Expected outcome based on the Elo difference
    - Sweep factor that reduces gain when the score gap is large
    - A per-set length bonus (total_games / 4) applied to both players
    - A fixed BO7 bonus (+2 / -2) when the winner's score is 4

    Returns (new_winner_elo, new_loser_elo).
    """
    result = calculate_elo_detailed(winner_elo, loser_elo, winner_score, loser_score)
    return result["new_winner_elo"], result["new_loser_elo"]


def calculate_elo_detailed(
    winner_elo: int,
    loser_elo: int,
    winner_score: int = 1,
    loser_score: int = 0,
) -> dict:
    """Calculate new Elo ratings with full breakdown for diagnostics.

    Returns a dict containing every intermediate value (useful for
    devmode formula display in -rp). All fields:

    - total_games, elo_diff, expected_lower, expected_winner, quotient
    - raw_gain, score_gap, sweep_factor, actual_gain
    - bonus_raw, bo7_winner_bonus, bo7_loser_bonus
    - winner_pre_round, loser_pre_round
    - new_winner_elo, new_loser_elo
    """
    total_games = winner_score + loser_score
    if total_games == 0:
        raise ValueError("total_games cannot be zero (winner_score + loser_score == 0)")

    elo_diff = abs(winner_elo - loser_elo)

    expected_lower = 1 / (1 + (10 ** (elo_diff / EXPECTED_OUTCOME_DIVISOR)))
    expected_winner = 1 - expected_lower if winner_elo >= loser_elo else expected_lower

    quotient = winner_score / total_games

    raw_gain = (
        RAW_GAIN_MULTIPLIER * total_games * (quotient - expected_winner)
        + SET_WIN_BONUS
    )

    score_gap = winner_score - loser_score
    sweep_factor = 1 - (SWEEP_REDUCTION_FACTOR * (score_gap - 1))
    actual_gain = raw_gain * sweep_factor

    bonus_raw = total_games / 4
    bo7_winner_bonus = BO7_FIXED_BONUS if winner_score == 4 else 0
    bo7_loser_bonus = -BO7_FIXED_BONUS if winner_score == 4 else 0

    winner_pre_round = winner_elo + actual_gain + bonus_raw + bo7_winner_bonus
    loser_pre_round = loser_elo - actual_gain + bonus_raw + bo7_loser_bonus

    return {
        "total_games": total_games,
        "elo_diff": elo_diff,
        "expected_lower": expected_lower,
        "expected_winner": expected_winner,
        "quotient": quotient,
        "raw_gain": raw_gain,
        "score_gap": score_gap,
        "sweep_factor": sweep_factor,
        "actual_gain": actual_gain,
        "bonus_raw": bonus_raw,
        "bo7_winner_bonus": bo7_winner_bonus,
        "bo7_loser_bonus": bo7_loser_bonus,
        "winner_pre_round": winner_pre_round,
        "loser_pre_round": loser_pre_round,
        "new_winner_elo": round(winner_pre_round),
        "new_loser_elo": round(loser_pre_round),
    }
