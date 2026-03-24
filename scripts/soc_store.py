#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import secrets
import socket
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RELATIONSHIP_LEVELS = ["L0", "L1", "L2", "L3", "L4"]
LEVEL_NAMES = {
    "L0": "初识",
    "L1": "普通",
    "L2": "熟识",
    "L3": "亲密",
    "L4": "共生",
}
SHARE_MIN_LEVEL = {
    "identity": "L0",
    "skills": "L1",
    "experience-summary": "L1",
    "task-summary": "L1",
    "cron-summary": "L2",
    "memory-summary": "L3",
    "soul-summary": "L3",
}
DEFAULT_STATE = {
    "identity": {},
    "peers": {},
    "pending": {"pairRequests": [], "upgradeRequests": []},
    "audit": {
        "lastSeenAt": None,
        "lastSharedAt": None,
        "lastMessageAt": None,
    },
    "settings": {
        "redactionKeywords": [],
        "logShares": True,
        "host": "0.0.0.0",
        "port": 45678,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_workspace_root() -> Path:
    env_root = os.environ.get("CLAWSOC_WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    script_root = Path(__file__).resolve().parent
    return script_root.parent.parent.parent.resolve()


@dataclass
class SocPaths:
    workspace_root: Path
    soc_dir: Path
    peers_dir: Path
    logs_dir: Path
    state_path: Path
    summary_path: Path


def get_paths(workspace_root: Path | None = None) -> SocPaths:
    root = (workspace_root or default_workspace_root()).resolve()
    soc_dir = root / "soc"
    return SocPaths(
        workspace_root=root,
        soc_dir=soc_dir,
        peers_dir=soc_dir / "peers",
        logs_dir=soc_dir / "logs",
        state_path=soc_dir / "state.json",
        summary_path=root / "soc.md",
    )


def ensure_layout(paths: SocPaths) -> None:
    paths.soc_dir.mkdir(parents=True, exist_ok=True)
    paths.peers_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)


def json_dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_state(paths: SocPaths) -> dict[str, Any]:
    ensure_layout(paths)
    if not paths.state_path.exists():
        return deepcopy(DEFAULT_STATE)
    return json.loads(paths.state_path.read_text(encoding="utf-8"))


def save_state(paths: SocPaths, state: dict[str, Any]) -> None:
    ensure_layout(paths)
    json_dump(paths.state_path, state)
    render_summary(paths, state)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def normalize_level(level: str) -> str:
    if level not in RELATIONSHIP_LEVELS:
        raise SystemExit(f"Unsupported relationship level: {level}")
    return level


def level_at_least(current: str, minimum: str) -> bool:
    return RELATIONSHIP_LEVELS.index(current) >= RELATIONSHIP_LEVELS.index(minimum)


def infer_local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def identity_from_env(host: str | None = None, port: int | None = None) -> dict[str, Any]:
    display_name = os.environ.get("CLAWSOC_NAME") or os.environ.get("OPENCLAW_NAME") or socket.gethostname()
    emoji = os.environ.get("CLAWSOC_EMOJI", "🐾")
    intro = os.environ.get("CLAWSOC_BIO", "一个正在学习社交协作的 Claw")
    bind_host = host or "0.0.0.0"
    if bind_host not in {"0.0.0.0", "::"}:
        real_host = bind_host
    else:
        real_host = os.environ.get("CLAWSOC_ADVERTISE_HOST") or infer_local_ip()
    bind_port = int(port or os.environ.get("CLAWSOC_PORT", 45678))
    peer_id = os.environ.get("CLAWSOC_ID") or f"claw-{uuid.uuid4().hex[:10]}"
    return {
        "id": peer_id,
        "displayName": display_name,
        "emoji": emoji,
        "bio": intro,
        "host": bind_host,
        "endpoint": f"http://{real_host}:{bind_port}",
        "createdAt": utc_now(),
        "secret": secrets.token_hex(16),
    }


def init_state(paths: SocPaths, *, host: str | None = None, port: int | None = None, force: bool = False) -> dict[str, Any]:
    ensure_layout(paths)
    state = load_state(paths)
    if force or not state.get("identity"):
        state["identity"] = identity_from_env(host=host, port=port)
    if host:
        state["settings"]["host"] = host
    if port:
        state["settings"]["port"] = int(port)
        if host and host not in {"0.0.0.0", "::"}:
            endpoint_host = host
        else:
            endpoint_host = os.environ.get("CLAWSOC_ADVERTISE_HOST") or infer_local_ip()
        state["identity"]["endpoint"] = f"http://{endpoint_host}:{int(port)}"
    state.setdefault("peers", {})
    state.setdefault("pending", {"pairRequests": [], "upgradeRequests": []})
    state.setdefault("audit", {"lastSeenAt": None, "lastSharedAt": None, "lastMessageAt": None})
    save_state(paths, state)
    return state


def get_peer_dir(paths: SocPaths, peer_id: str) -> Path:
    return paths.peers_dir / peer_id


def ensure_peer_layout(paths: SocPaths, peer_id: str) -> Path:
    peer_dir = get_peer_dir(paths, peer_id)
    (peer_dir / "shares").mkdir(parents=True, exist_ok=True)
    return peer_dir


def upsert_peer(paths: SocPaths, state: dict[str, Any], peer: dict[str, Any]) -> dict[str, Any]:
    peer_id = peer["peerId"]
    current = state["peers"].get(peer_id, {})
    merged = {
        "peerId": peer_id,
        "nickname": peer.get("nickname") or current.get("nickname") or peer_id,
        "displayName": peer.get("displayName") or current.get("displayName") or peer_id,
        "emoji": peer.get("emoji") or current.get("emoji") or "🐾",
        "bio": peer.get("bio") or current.get("bio") or "",
        "endpoint": peer["endpoint"],
        "relationshipLevel": normalize_level(peer.get("relationshipLevel") or current.get("relationshipLevel") or "L0"),
        "status": peer.get("status") or current.get("status") or "active",
        "createdAt": current.get("createdAt") or utc_now(),
        "updatedAt": utc_now(),
        "lastSeenAt": peer.get("lastSeenAt") or current.get("lastSeenAt"),
        "lastSharedAt": peer.get("lastSharedAt") or current.get("lastSharedAt"),
        "lastMessageAt": peer.get("lastMessageAt") or current.get("lastMessageAt"),
        "notes": peer.get("notes") or current.get("notes") or "",
    }
    state["peers"][peer_id] = merged
    peer_dir = ensure_peer_layout(paths, peer_id)
    json_dump(peer_dir / "profile.json", merged)
    return merged


def log_event(paths: SocPaths, kind: str, payload: dict[str, Any]) -> None:
    append_jsonl(paths.logs_dir / "events.jsonl", {"timestamp": utc_now(), "kind": kind, "payload": payload})


def log_message(paths: SocPaths, peer_id: str, direction: str, message: str, request_id: str, metadata: dict[str, Any] | None = None) -> None:
    peer_dir = ensure_peer_layout(paths, peer_id)
    append_jsonl(
        peer_dir / "messages.jsonl",
        {
            "timestamp": utc_now(),
            "direction": direction,
            "peerId": peer_id,
            "requestId": request_id,
            "message": message,
            "metadata": metadata or {},
        },
    )


def log_share(paths: SocPaths, peer_id: str, share_type: str, content: dict[str, Any], request_id: str) -> None:
    peer_dir = ensure_peer_layout(paths, peer_id)
    safe_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_dump(peer_dir / "shares" / f"{safe_stamp}-{share_type}.json", content)
    append_jsonl(
        paths.logs_dir / "events.jsonl",
        {
            "timestamp": utc_now(),
            "kind": "share",
            "payload": {
                "peerId": peer_id,
                "shareType": share_type,
                "requestId": request_id,
            },
        },
    )


def render_summary(paths: SocPaths, state: dict[str, Any]) -> None:
    identity = state.get("identity", {})
    peers = sorted(state.get("peers", {}).values(), key=lambda item: item.get("updatedAt") or "", reverse=True)
    pending_pairs = state.get("pending", {}).get("pairRequests", [])
    pending_upgrades = state.get("pending", {}).get("upgradeRequests", [])
    events_path = paths.logs_dir / "events.jsonl"
    recent_events: list[dict[str, Any]] = []
    if events_path.exists():
        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        for line in lines[-8:]:
            if line.strip():
                recent_events.append(json.loads(line))
    lines = [
        "# soc.md — ClawSoc 社交数据",
        "",
        "## 我的社交身份",
        f"- ID: {identity.get('id', '-')}",
        f"- 名称: {identity.get('displayName', '-')}",
        f"- 表情: {identity.get('emoji', '-')}",
        f"- 简介: {identity.get('bio', '-')}",
        f"- Endpoint: {identity.get('endpoint', '-')}",
        f"- 创建时间: {identity.get('createdAt', '-')}",
        "",
        "## 当前关系概览",
    ]
    if peers:
        for peer in peers:
            lines.extend(
                [
                    f"### {peer['peerId']} · {peer.get('displayName') or peer['peerId']}",
                    f"- 层级: {peer['relationshipLevel']} {LEVEL_NAMES.get(peer['relationshipLevel'], '')}",
                    f"- Endpoint: {peer.get('endpoint', '-')}",
                    f"- 状态: {peer.get('status', '-')}",
                    f"- 最后消息: {peer.get('lastMessageAt') or '-'}",
                    f"- 最后分享: {peer.get('lastSharedAt') or '-'}",
                    "",
                ]
            )
    else:
        lines.extend(["- 暂无已配对 Claw", ""])
    lines.append("## 待处理升级/配对请求")
    if pending_pairs or pending_upgrades:
        for req in pending_pairs:
            lines.append(f"- 配对请求 {req['requestId']} 来自 {req['fromPeerId']} ({req.get('createdAt', '-')})")
        for req in pending_upgrades:
            lines.append(
                f"- 升级请求 {req['requestId']} {req['fromPeerId']} -> {req['targetLevel']} ({req.get('status', 'pending')})"
            )
    else:
        lines.append("- 暂无待处理请求")
    lines.extend(["", "## 最近互动"])
    if recent_events:
        for event in recent_events:
            payload = event.get("payload", {})
            lines.append(f"- {event.get('timestamp')} · {event.get('kind')} · {json.dumps(payload, ensure_ascii=False)}")
    else:
        lines.append("- 暂无互动记录")
    lines.extend(["", "## 最近分享记录"])
    share_events = [event for event in recent_events if event.get("kind") in {"share", "share.sent"}]
    if share_events:
        for event in share_events:
            payload = event.get("payload", {})
            lines.append(f"- {event.get('timestamp')} 向 {payload.get('peerId')} 分享 {payload.get('shareType')}")
    else:
        lines.append("- 暂无分享记录")
    paths.summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
