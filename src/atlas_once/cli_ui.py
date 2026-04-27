from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Literal, TextIO

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

Align = Literal["left", "right"]
Color = Literal["red", "green", "yellow", "blue", "magenta", "cyan", "muted", "bold"]

_ANSI_CODES: dict[Color, str] = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "muted": "2",
    "bold": "1",
}


@dataclass(frozen=True)
class Cell:
    text: str
    color: Color | None = None


@dataclass(frozen=True)
class Column:
    key: str
    header: str
    align: Align = "left"
    min_width: int = 0


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def visible_width(text: str) -> int:
    return len(strip_ansi(text))


def color_enabled(stream: TextIO | None = None) -> bool:
    if os.environ.get("CLICOLOR_FORCE") not in {None, "", "0"}:
        return True
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("CLICOLOR") == "0":
        return False
    output = stream if stream is not None else sys.stdout
    return output.isatty()


def colorize(text: str, color: Color | None, *, enabled: bool | None = None) -> str:
    if color is None:
        return text
    use_color = color_enabled() if enabled is None else enabled
    if not use_color:
        return text
    return f"\x1b[{_ANSI_CODES[color]}m{text}\x1b[0m"


def _cell_parts(value: Any) -> tuple[str, Color | None]:
    if isinstance(value, Cell):
        return value.text, value.color
    if value is None:
        return "", None
    return str(value), None


def _pad(text: str, width: int, align: Align) -> str:
    padding = max(0, width - visible_width(text))
    if align == "right":
        return " " * padding + text
    return text + " " * padding


def render_table(
    rows: list[dict[str, Any]],
    columns: list[Column],
    *,
    color: bool | None = None,
    header: bool = True,
    gap: str = "  ",
) -> str:
    if not columns:
        return ""

    widths: dict[str, int] = {}
    for column in columns:
        width = max(column.min_width, visible_width(column.header))
        for row in rows:
            text, _ = _cell_parts(row.get(column.key, ""))
            width = max(width, visible_width(text))
        widths[column.key] = width

    use_color = color_enabled() if color is None else color
    rendered: list[str] = []
    if header:
        header_cells = [
            colorize(
                _pad(column.header, widths[column.key], column.align),
                "bold",
                enabled=use_color,
            )
            for column in columns
        ]
        rendered.append(gap.join(header_cells).rstrip())

    for row in rows:
        cells: list[str] = []
        for column in columns:
            text, cell_color = _cell_parts(row.get(column.key, ""))
            padded = _pad(text, widths[column.key], column.align)
            cells.append(colorize(padded, cell_color, enabled=use_color))
        rendered.append(gap.join(cells).rstrip())
    return "\n".join(rendered)
