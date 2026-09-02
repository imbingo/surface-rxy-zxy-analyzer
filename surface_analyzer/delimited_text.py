"""Shared single-physical-line tokenizer for delimited engineering data."""

from __future__ import annotations

import csv
import re
from typing import Iterable


FIXED_DELIMITERS = ("\t", ",", ";", "；", "|")
WHITESPACE_SEPARATORS = frozenset(("whitespace", r"\s+"))


class DelimitedLineError(ValueError):
    """Raised when one physical record contains invalid CSV-style quoting."""


def tokenize_delimited_line(line: str, separator: str) -> list[str]:
    """Tokenize one physical record while preserving logical empty fields.

    Fixed one-character separators use Python's CSV quoting rules.  Embedded
    physical newlines inside quoted fields are deliberately outside this
    device-file contract; callers pass one physical line at a time.
    """

    text = str(line).rstrip("\r\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    if separator in WHITESPACE_SEPARATORS:
        return [token.strip() for token in re.split(r"\s+", text.strip()) if token]
    if not isinstance(separator, str) or len(separator) != 1:
        raise ValueError(f"Unsupported delimiter: {separator!r}")
    try:
        tokens = next(csv.reader(
            [text], delimiter=separator, quotechar='"', doublequote=True,
            skipinitialspace=False, strict=True,
        ))
    except csv.Error as exc:
        raise DelimitedLineError(f"CSV quoting error: {exc}") from exc
    return [token.strip() for token in tokens]


def detect_delimiter(line: str, candidates: Iterable[str] = FIXED_DELIMITERS) -> str:
    """Choose the delimiter producing the widest valid logical record.

    This is quote-aware: delimiter characters inside a quoted field do not
    increase the logical field count.  Stable-run validation remains the
    caller's responsibility because one metadata line is not enough to infer
    a complete file layout.
    """

    text = str(line).rstrip("\r\n")
    best_separator = r"\s+"
    best_width = 1
    for separator in candidates:
        try:
            width = len(tokenize_delimited_line(text, separator))
        except (DelimitedLineError, ValueError):
            continue
        if width > best_width:
            best_separator = separator
            best_width = width
    if best_width > 1:
        return best_separator
    whitespace_width = len(tokenize_delimited_line(text, r"\s+"))
    return r"\s+" if whitespace_width > 1 else best_separator
