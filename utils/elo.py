import math


def calculate_win_rate(games_won: int, games_lost: int) -> float:
    total_games = games_won + games_lost
    return round((games_won / total_games) * 100, 2) if total_games > 0 else 0.0


def calculate_elo(
    winner_elo: int,
    loser_elo: int,
    total_games: int = 1,
    winner_score: int = 1,
    loser_score: int = 0,
):
    elo_diff = winner_elo - loser_elo

    expected_wr_loser = 1 / (1 + math.pow(10, (elo_diff / 1147.44429731658)))
    expected_wr_winner = 1 - expected_wr_loser

    win_quotient = winner_score / total_games

    raw_gain = 20 * total_games * (win_quotient - expected_wr_winner) + 5

    sweep_factor = 1 - (0.076923 * (winner_score - loser_score - 1))
    actual_gain = raw_gain * sweep_factor if sweep_factor > 0 else 0

    new_winner_elo = winner_elo + round(actual_gain)
    new_loser_elo = loser_elo - round(actual_gain)

    # bo7 bonus
    if winner_score == 4:
        new_winner_elo += 2
        new_loser_elo -= 2

    return new_winner_elo, new_loser_elo

