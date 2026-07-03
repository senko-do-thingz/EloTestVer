"""Tests for history table formatting."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.match_history_db import MatchHistoryRow
from utils.history_format import format_history_table


def test_format_history_table():
    rows = [
        MatchHistoryRow(
            match_id=1,
            player_id="1",
            opponent_name="Bob",
            wins=3,
            losses=1,
            elo_init=500,
            elo_final=542,
            played_at=1_735_689_600,
        )
    ]
    text = format_history_table(rows, player_name="Alice", total_matches=1)
    assert "Alice" in text
    assert "Bob" in text
    assert "+42" in text
    assert "Date" in text
    assert "Opponent" in text
