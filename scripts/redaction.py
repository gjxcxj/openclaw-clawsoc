#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{32,}={0,2}\b"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s]+"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\d[ -]*?){12,19}\b"),
    re.compile(r"/Users/[^\s/]+"),
    re.compile(r"\bpostgres(?:ql)?://[^\s]+", re.IGNORECASE),
]


def redact_text(text: str, extra_keywords: list[str] | None = None) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    for keyword in extra_keywords or []:
        if keyword:
            result = result.replace(keyword, "[REDACTED]")
    return result


def redact_file(path: Path, extra_keywords: list[str] | None = None, limit_chars: int | None = None) -> str:
    content = path.read_text(encoding="utf-8", errors="ignore")
    if limit_chars:
        content = content[:limit_chars]
    return redact_text(content, extra_keywords=extra_keywords)
