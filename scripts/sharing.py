#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from redaction import redact_file, redact_text
from soc_store import SHARE_MIN_LEVEL


def allowed_share_types() -> list[str]:
    return sorted(SHARE_MIN_LEVEL.keys())


def summarize_skills(workspace_root: Path) -> list[dict[str, Any]]:
    skills_root = workspace_root / "skills"
    results = []
    if not skills_root.exists():
        return results
    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        description = ""
        for line in skill_md.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("---") and not stripped.startswith("#"):
                description = stripped[:140]
                break
        results.append({"name": skill_dir.name, "description": description})
    return results


def summarize_tasks(workspace_root: Path) -> list[dict[str, Any]]:
    automations_root = workspace_root.parent / "automations"
    items = []
    if automations_root.exists():
        for automation in sorted(automations_root.glob("*/automation.toml")):
            lines = automation.read_text(encoding="utf-8", errors="ignore").splitlines()
            name = automation.parent.name
            rrule = ""
            for line in lines:
                if line.startswith("name"):
                    name = line.split("=", 1)[1].strip().strip('"')
                if line.startswith("rrule"):
                    rrule = line.split("=", 1)[1].strip().strip('"')
            items.append({"name": name, "schedule": rrule or "unknown"})
    return items


def memory_summary(workspace_root: Path, extra_keywords: list[str]) -> str:
    memory_root = workspace_root / "memory"
    snippets = []
    for path in sorted(memory_root.glob("*.md"))[:3]:
        snippets.append(f"## {path.name}\n{redact_file(path, extra_keywords=extra_keywords, limit_chars=400)}")
    return "\n\n".join(snippets) if snippets else "暂无可分享的 memory 摘要"


def soul_summary(workspace_root: Path, extra_keywords: list[str]) -> str:
    for candidate in [workspace_root / "SOUL.md", workspace_root / "soul.md", workspace_root / "IDENTITY.md"]:
        if candidate.exists():
            return redact_file(candidate, extra_keywords=extra_keywords, limit_chars=1200)
    return "未找到 SOUL/IDENTITY 文件"


def experience_summary(workspace_root: Path, extra_keywords: list[str]) -> str:
    candidate = workspace_root / "experience.md"
    if not candidate.exists():
        return "未找到 experience.md"
    return redact_file(candidate, extra_keywords=extra_keywords, limit_chars=1800)


def identity_summary(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": identity.get("id"),
        "displayName": identity.get("displayName"),
        "emoji": identity.get("emoji"),
        "bio": identity.get("bio"),
        "endpoint": identity.get("endpoint"),
    }


def build_share_content(
    share_type: str,
    *,
    workspace_root: Path,
    identity: dict[str, Any],
    extra_keywords: list[str],
) -> dict[str, Any]:
    if share_type == "identity":
        return identity_summary(identity)
    if share_type == "skills":
        return {"skills": summarize_skills(workspace_root)}
    if share_type == "experience-summary":
        return {"summary": experience_summary(workspace_root, extra_keywords)}
    if share_type == "task-summary":
        tasks = summarize_tasks(workspace_root)
        return {"tasks": [item["name"] for item in tasks]}
    if share_type == "cron-summary":
        return {"tasks": summarize_tasks(workspace_root)}
    if share_type == "memory-summary":
        return {"summary": memory_summary(workspace_root, extra_keywords)}
    if share_type == "soul-summary":
        return {"summary": soul_summary(workspace_root, extra_keywords)}
    raise SystemExit(f"Unsupported share type: {share_type}")


def sanitize_share_content(content: dict[str, Any], extra_keywords: list[str]) -> dict[str, Any]:
    raw = json.dumps(content, ensure_ascii=False)
    return json.loads(redact_text(raw, extra_keywords=extra_keywords))
