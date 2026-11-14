"""Human-friendly UTC-relative time formatting."""

from datetime import datetime, timezone


def format_relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        seconds = 0

    if seconds < 60:
        return f"{seconds} seconds ago" if seconds >= 5 else "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minutes ago" if minutes > 1 else "1 minute ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago" if hours > 1 else "1 hour ago"
    days = hours // 24
    return f"{days} days ago" if days > 1 else "1 day ago"
