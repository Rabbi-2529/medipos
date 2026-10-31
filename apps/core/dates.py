"""Expiry dates as people write them: 2027-02, 02/2027, Feb-27, 28/02/2027 …"""
import calendar
import re
from datetime import date, datetime

MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_abbr) if name}


def _end_of_month(year, month):
    return date(year, month, calendar.monthrange(year, month)[1])


def _year(text):
    year = int(text)
    return year + 2000 if year < 100 else year


def parse_expiry(value):
    """A date, or None for blank. Month-only values mean the last day of that month. Raises ValueError."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", text)                      # 2027-02
    if match:
        return _end_of_month(int(match[1]), int(match[2]))
    match = re.fullmatch(r"(\d{1,2})[-/.](\d{2}|\d{4})", text)                # 02/2027, 2/27
    if match and 1 <= int(match[1]) <= 12:
        return _end_of_month(_year(match[2]), int(match[1]))
    match = re.fullmatch(r"([A-Za-z]{3})[A-Za-z]*[-/ .']*(\d{2}|\d{4})", text)  # Feb-27, February 2027
    if match and match[1].lower() in MONTHS:
        return _end_of_month(_year(match[2]), MONTHS[match[1].lower()])
    raise ValueError(f"can't read the date “{text}” (use e.g. 2027-02 or 02/2027)")
