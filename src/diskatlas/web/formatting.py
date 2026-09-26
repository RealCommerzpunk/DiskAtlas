"""Jinja-Filter für Größen, Zeiten und Status."""

from __future__ import annotations

from datetime import UTC, datetime

from jinja2 import Environment

SIZE_UNITS = ["B", "kB", "MB", "GB", "TB", "PB"]


def filesize(value: int | None) -> str:
    """Dezimale Einheiten – wie auf dem Etikett der Festplatte."""
    if value is None:
        return "–"
    size = float(value)
    for unit in SIZE_UNITS:
        if abs(size) < 1000 or unit == SIZE_UNITS[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".", ",")
        size /= 1000
    return f"{value} B"  # pragma: no cover


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone()


def datetime_fmt(value: datetime | None) -> str:
    return _local(value).strftime("%d.%m.%Y %H:%M") if value else "–"


def date_fmt(value: datetime | None) -> str:
    return _local(value).strftime("%d.%m.%Y") if value else "–"


def ago(value: datetime | None) -> str:
    if value is None:
        return "nie"
    seconds = (datetime.now(UTC) - _local(value)).total_seconds()
    if seconds < 90:
        return "gerade eben"
    for limit, div, unit_one, unit_many in (
        (5400, 60, "Minute", "Minuten"),
        (129600, 3600, "Stunde", "Stunden"),
        (86400 * 60, 86400, "Tag", "Tagen"),
        (86400 * 730, 86400 * 30, "Monat", "Monaten"),
    ):
        if seconds < limit:
            n = round(seconds / div)
            return f"vor {n} {unit_one if n == 1 else unit_many}"
    n = round(seconds / (86400 * 365))
    return f"vor {n} Jahren"


def number(value: int | float | None) -> str:
    if value is None:
        return "–"
    return f"{value:,.0f}".replace(",", ".")


def percent(value: float | None) -> str:
    return "–" if value is None else f"{value:.0f} %"


def text_color(hex_color: str) -> str:
    """Schwarz oder Weiß – je nachdem, was auf der Label-Farbe besser lesbar ist."""
    try:
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    except (ValueError, TypeError):
        return "#fff"
    return "#111" if (0.299 * r + 0.587 * g + 0.114 * b) > 160 else "#fff"


def register(env: Environment) -> None:
    env.filters.update(
        filesize=filesize,
        dt=datetime_fmt,
        date=date_fmt,
        ago=ago,
        number=number,
        percent=percent,
        text_color=text_color,
    )
