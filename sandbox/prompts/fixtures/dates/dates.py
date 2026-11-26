"""Birthday helpers."""
from datetime import date


def days_until_birthday(birthday: date, today: date) -> int:
    """Number of days from today until the next birthday.

    Rules:
    - 0 when the birthday is today.
    - Otherwise the days until the next occurrence of the month and day.
    - A February 29 birthday is celebrated on February 28 in years that are not leap years.
    """
    year = today.year
    next_birthday = birthday.replace(year=year)
    if next_birthday <= today:
        next_birthday = birthday.replace(year=year + 1)
    return (next_birthday - today).days
