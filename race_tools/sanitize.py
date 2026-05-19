"""
String sanitization and formatting utilities for race data.
"""
import re
import pandas as pd


def sanitize_filename(text: str | None) -> str:
    """
    Sanitize strings for filenames (lowercase, underscores, alphanumeric).

    Args:
        text: The string to sanitize.

    Returns:
        A sanitized string safe for use as a filename.
    """
    if not text:
        return ''
    text = text.lower().strip()
    text = re.sub(r'[\s\-]+', '_', text)
    text = re.sub(r'[^a-z0-9_]', '', text)
    return re.sub(r'_+', '_', text).strip('_')


def strip_race_prefix(session_name: str) -> str:
    """
    Removes 'Race N:' prefix from session name.

    Args:
        session_name: The raw session name string.

    Returns:
        The session name without the race prefix.
    """
    return re.sub(r'^Race \d+:\s*', '', session_name)


def humanize_track_name(text: str) -> str:
    """
    Make track name more human readable.

    Args:
        text: The raw track name string.

    Returns:
        A cleaned, title-cased track name.
    """
    text = text.replace('_', ' ')
    text = re.sub(r'(?i)direct drive circuit', '', text)
    text = re.sub(r'(?i)kart circuit', '', text)
    text = re.sub(r'(?i)raceway', '', text)
    text = ' '.join(text.split())
    return text.title()


def parse_time(time_str: str | float | None) -> float | None:
    """
    Returns the number of seconds in a time formatted like MM:SS.sss or a numeric string.

    Args:
        time_str: The time string or number to parse.

    Returns:
        The time in seconds as a float, or None if parsing fails.
    """
    if time_str is None or pd.isna(time_str):
        return None
    time_str = str(time_str).strip()
    if not time_str or time_str == '-':
        return None
    try:
        if ':' in time_str:
            parts = time_str.split(':')
            if len(parts) == 2:
                minutes, seconds = parts
                return int(minutes) * 60 + float(seconds)
        return float(time_str)
    except (ValueError, TypeError):
        return None


def is_final_session(session_name: str) -> bool:
    """
    Checks if a session is a final or a heat.

    Args:
        session_name: The session name to check.

    Returns:
        True if the session is a final or heat, False otherwise.
    """
    name_str = str(session_name).lower()
    return 'final' in name_str or 'heat' in name_str


def clean_slug(s: str) -> str:
    """
    Create a clean slug from a string (lowercase, alphanumeric and underscores).

    Args:
        s: The string to slugify.

    Returns:
        A clean slug string.
    """
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')
