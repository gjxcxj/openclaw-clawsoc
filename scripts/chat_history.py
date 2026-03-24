#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def load_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def render_history(records: list[dict], limit: int = 20) -> str:
    if not records:
        return "暂无历史消息"
    lines = []
    for record in records[-limit:]:
        arrow = "->" if record.get("direction") == "outbound" else "<-"
        lines.append(f"{record.get('timestamp')} {arrow} {record.get('message')}")
    return "\n".join(lines)
