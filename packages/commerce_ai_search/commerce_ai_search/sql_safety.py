from __future__ import annotations

import re


FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b(alter|backup|begin|commit|create|dbcc|declare|delete|deny|drop|exec|execute|grant|insert|into|merge|restore|"
    r"rollback|save|set|truncate|update|use)\b|(?:\b(?:sp|xp)_\w+)",
    re.IGNORECASE,
)
SQL_COMMENT_PATTERN = re.compile(r"(--|/\*)")


def validate_readonly_query(query: str) -> None:
    stripped = query.strip()
    if not stripped:
        raise ValueError("MSSQL query must not be empty")
    if SQL_COMMENT_PATTERN.search(stripped):
        raise ValueError("MSSQL query must not contain comments")
    without_trailing_semicolon = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in without_trailing_semicolon:
        raise ValueError("MSSQL query must contain a single SELECT statement")
    normalized = " ".join(without_trailing_semicolon.split()).lower()
    if not normalized.startswith(("select ", "with ")):
        raise ValueError("MSSQL query must start with SELECT or WITH")
    match = FORBIDDEN_SQL_PATTERN.search(normalized)
    if match:
        raise ValueError(f"MSSQL query contains a forbidden keyword: {match.group(1) or match.group(0)}")


def clean_readonly_query(query: str) -> str:
    validate_readonly_query(query)
    return query.strip().rstrip(";").strip()
