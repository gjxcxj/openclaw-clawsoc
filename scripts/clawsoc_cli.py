#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from chat_history import load_history, render_history
from pairing import build_invite, parse_invite
from peer_server import serve
from sharing import allowed_share_types, build_share_content, sanitize_share_content
from soc_store import (
    LEVEL_NAMES,
    SHARE_MIN_LEVEL,
    get_paths,
    init_state,
    level_at_least,
    load_state,
    log_event,
    log_message,
    log_share,
    normalize_level,
    save_state,
    upsert_peer,
    utc_now,
)

SHARE_TYPE_ALIASES = {
    "identity": "identity",
    "身份": "identity",
    "身份摘要": "identity",
    "skills": "skills",
    "技能": "skills",
    "技能列表": "skills",
    "experience-summary": "experience-summary",
    "经验": "experience-summary",
    "经验摘要": "experience-summary",
    "经验教训": "experience-summary",
    "task-summary": "task-summary",
    "任务概要": "task-summary",
    "cron-summary": "cron-summary",
    "cron概要": "cron-summary",
    "定时任务概要": "cron-summary",
    "memory-summary": "memory-summary",
    "记忆": "memory-summary",
    "记忆摘要": "memory-summary",
    "soul-summary": "soul-summary",
    "灵魂": "soul-summary",
    "灵魂摘要": "soul-summary",
}


def request_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise SystemExit(f"HTTP {exc.code}: {body or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error: {exc.reason}") from exc


def healthcheck(url: str) -> dict:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise SystemExit(f"Peer unreachable: {exc.reason}") from exc


def probe_health(url: str, timeout: float = 0.6) -> dict | None:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def make_envelope(peer_id: str, msg_type: str, payload: dict) -> dict:
    return {
        "fromPeerId": peer_id,
        "timestamp": utc_now(),
        "requestId": uuid.uuid4().hex,
        "type": msg_type,
        "payload": payload,
    }


def cmd_init(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = init_state(paths, host=args.host, port=args.port, force=args.force)
    print(json.dumps({"ok": True, "identity": state["identity"], "statePath": str(paths.state_path)}, ensure_ascii=False, indent=2))


def cmd_identity(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    print(json.dumps(state["identity"], ensure_ascii=False, indent=2))


def cmd_invite(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    print(build_invite(state["identity"]["id"], state["identity"]["endpoint"], ttl_minutes=args.ttl))


def _candidate_hosts(state: dict, args: argparse.Namespace) -> list[str]:
    explicit_hosts = [host.strip() for host in (args.hosts or "").split(",") if host.strip()]
    if explicit_hosts:
        return explicit_hosts
    if args.cidr:
        network = ipaddress.ip_network(args.cidr, strict=False)
        return [str(host) for host in network.hosts()]

    endpoint = state.get("identity", {}).get("endpoint", "")
    host = "127.0.0.1"
    try:
        host = endpoint.split("://", 1)[1].rsplit(":", 1)[0]
    except IndexError:
        pass

    if host == "127.0.0.1":
        return ["127.0.0.1"]

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return [host]

    if ip.version != 4:
        return [host]
    network = ipaddress.ip_network(f"{host}/24", strict=False)
    return [str(candidate) for candidate in network.hosts()]


def _discover_peers(paths, state: dict, args: argparse.Namespace) -> dict[str, dict]:
    hosts = _candidate_hosts(state, args)
    ports = sorted({int(port.strip()) for port in (args.ports or "45678").split(",") if port.strip()})
    self_id = state["identity"]["id"]
    targets = [(host, port) for host in hosts for port in ports]

    def _probe(target: tuple[str, int]) -> dict | None:
        host, port = target
        url = f"http://{host}:{port}/clawsoc/health"
        payload = probe_health(url, timeout=args.timeout)
        if not payload or not payload.get("ok"):
            return None
        if payload.get("peerId") == self_id:
            return None
        endpoint = payload.get("endpoint") or f"http://{host}:{port}"
        return {
            "peerId": payload.get("peerId"),
            "displayName": payload.get("displayName") or payload.get("peerId"),
            "emoji": payload.get("emoji") or "🐾",
            "bio": payload.get("bio") or "",
            "endpoint": endpoint,
            "host": host,
            "port": port,
            "invite": build_invite(payload.get("peerId"), endpoint, ttl_minutes=10),
        }

    discovered: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, args.workers)) as executor:
        for result in executor.map(_probe, targets):
            if result:
                discovered.append(result)

    deduped: dict[str, dict] = {}
    for item in discovered:
        deduped[item["peerId"]] = item

    cli_path = Path(__file__).resolve()
    for peer in deduped.values():
        peer["pairCommand"] = f"python3 {cli_path} pair-direct {peer['peerId']}"
        peer["pairWithInviteCommand"] = f'python3 {cli_path} pair "{peer["invite"]}"'
        existing = state.get("peers", {}).get(peer["peerId"], {})
        peer["recordedStatus"] = existing.get("status", "unknown")
        peer["recordedLevel"] = existing.get("relationshipLevel", "L0")
    return deduped


def cmd_discover(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    deduped = _discover_peers(paths, state, args)

    if args.record:
        for peer in deduped.values():
            upsert_peer(
                paths,
                state,
                {
                    "peerId": peer["peerId"],
                    "displayName": peer["displayName"],
                    "nickname": peer["displayName"],
                    "emoji": peer["emoji"],
                    "bio": peer["bio"],
                    "endpoint": peer["endpoint"],
                    "relationshipLevel": state.get("peers", {}).get(peer["peerId"], {}).get("relationshipLevel", "L0"),
                    "status": state.get("peers", {}).get(peer["peerId"], {}).get("status", "discovered"),
                    "lastSeenAt": utc_now(),
                },
            )
        if deduped:
            log_event(paths, "discover.recorded", {"count": len(deduped), "peerIds": sorted(deduped)})
            save_state(paths, state)

    print(json.dumps({"ok": True, "count": len(deduped), "peers": list(deduped.values())}, ensure_ascii=False, indent=2))


def cmd_pair(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    print(json.dumps(_pair_with_invite(paths, state, args.invite), ensure_ascii=False, indent=2))


def _pair_with_invite(paths, state: dict, invite_token: str) -> None:
    invite = parse_invite(invite_token)
    healthcheck(f"{invite['endpoint']}/clawsoc/health")
    envelope = make_envelope(
        state["identity"]["id"],
        "pair.request",
        {
            "displayName": state["identity"]["displayName"],
            "emoji": state["identity"].get("emoji"),
            "bio": state["identity"].get("bio"),
            "endpoint": state["identity"]["endpoint"],
        },
    )
    response = request_json(f"{invite['endpoint']}/clawsoc/pair", envelope)
    remote = response["identity"]
    existing = state.get("peers", {}).get(remote["peerId"], {})
    peer = upsert_peer(
        paths,
        state,
        {
            "peerId": remote["peerId"],
            "displayName": remote["displayName"],
            "nickname": remote["displayName"],
            "emoji": remote.get("emoji"),
            "bio": remote.get("bio"),
            "endpoint": remote["endpoint"],
            "relationshipLevel": existing.get("relationshipLevel", "L0"),
            "status": "active",
            "lastSeenAt": utc_now(),
        },
    )
    log_event(paths, "pair.requested", {"peerId": peer["peerId"], "requestId": envelope["requestId"]})
    save_state(paths, state)
    return {"ok": True, "peer": peer}


def cmd_pair_direct(args: argparse.Namespace) -> None:
    result = _pair_direct(
        Path(args.workspace_root).resolve() if args.workspace_root else None,
        args.peer_id,
        ttl=args.ttl,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _pair_direct(workspace_root: Path | None, peer_id: str, *, ttl: int) -> dict:
    paths = get_paths(workspace_root)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_peer_or_die(state, peer_id, require_known=True)
    if peer.get("status") == "active":
        return {"ok": True, "peer": peer, "message": "Peer already paired"}
    invite = build_invite(peer["peerId"], peer["endpoint"], ttl_minutes=ttl)
    return _pair_with_invite(paths, state, invite)


def _copy_to_clipboard(text: str) -> bool:
    for program in ("pbcopy",):
        try:
            subprocess.run([program], input=text.encode("utf-8"), check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


def _recent_share_summaries(paths, peer_id: str, limit: int = 2) -> list[str]:
    share_dir = paths.peers_dir / peer_id / "shares"
    if not share_dir.exists():
        return []
    items = sorted(share_dir.glob("*.json"), reverse=True)[:limit]
    summaries = []
    for path in items:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        share_type = data.get("shareType") or path.stem.split("-", 1)[-1]
        content = data.get("content", {})
        preview = ""
        if isinstance(content, dict):
            if "summary" in content:
                preview = str(content["summary"])[:60]
            elif "skills" in content:
                preview = f"{len(content['skills'])} 个技能"
            elif "tasks" in content:
                preview = f"{len(content['tasks'])} 个任务"
            else:
                preview = json.dumps(content, ensure_ascii=False)[:60]
        summaries.append(f"{share_type}: {preview or '已共享'}")
    return summaries


def _available_quick_shares(level: str) -> list[str]:
    available = []
    for share_type, minimum in SHARE_MIN_LEVEL.items():
        if level_at_least(level, minimum):
            available.append(share_type)
    return available


def _render_chat_panel(paths, peer_id: str, limit: int = 12) -> str:
    state = load_state(paths)
    peer = _get_peer_or_die(state, peer_id)
    history_path = paths.peers_dir / peer["peerId"] / "messages.jsonl"
    history = render_history(load_history(history_path), limit=limit)
    level = peer.get("relationshipLevel", "L0")
    level_name = LEVEL_NAMES.get(level, level)
    shares = _recent_share_summaries(paths, peer["peerId"])
    quick_shares = ", ".join(_available_quick_shares(level)) or "无"
    title = f"\n聊天页 · {peer.get('displayName') or peer['peerId']} [{peer['peerId']}]"
    share_lines = shares if shares else ["暂无最近分享"]
    return "\n".join(
        [
            title,
            "-" * 72,
            f"关系等级: {level} {level_name}    状态: {peer.get('status', 'unknown')}",
            f"快捷分享: {quick_shares}",
            "最近分享:",
            *(f"- {item}" for item in share_lines),
            "-" * 72,
            history,
            "-" * 72,
            "输入消息直接发送；/share <类型> 快捷分享；/r 刷新历史；/q 返回发现页",
        ]
    )


def _open_chat_panel(paths, peer_id: str) -> None:
    while True:
        print(_render_chat_panel(paths, peer_id), flush=True)
        raw = input("chat> ").rstrip("\n")
        if not raw:
            continue
        if raw.strip() == "/q":
            return
        if raw.strip() == "/r":
            continue
        if raw.startswith("/share "):
            share_type = raw.split(maxsplit=1)[1].strip()
            share_args = argparse.Namespace(
                workspace_root=str(paths.workspace_root),
                share_type=share_type,
                peer_id=peer_id,
                item="",
            )
            try:
                cmd_share(share_args)
            except SystemExit as exc:
                print(str(exc), flush=True)
            continue
        chat_args = argparse.Namespace(
            workspace_root=str(paths.workspace_root),
            peer_id=peer_id,
            message=raw,
        )
        try:
            _send_chat_message(chat_args)
        except SystemExit as exc:
            print(str(exc), flush=True)


def _render_discover_table(peers: list[dict]) -> str:
    if not peers:
        return "未发现任何 ClawSoc 实例。"
    lines = ["\n发现结果", "-" * 72]
    for idx, peer in enumerate(peers, start=1):
        status = peer.get("recordedStatus", "unknown")
        level = peer.get("recordedLevel", "L0")
        lines.append(
            f"[{idx}] {peer['displayName']} [{peer['peerId']}]  {status}/{level}  {peer['endpoint']}"
        )
        if peer.get("bio"):
            lines.append(f"     简介: {peer['bio']}")
    lines.extend(
        [
            "-" * 72,
            "操作: p <序号>=一键配对并进入聊天, c <序号>=复制邀请码, i <序号>=显示邀请码",
            "      m <序号>=显示配对命令, r=重新扫描, q=退出",
        ]
    )
    return "\n".join(lines)


def cmd_discover_ui(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)

    while True:
        state = load_state(paths)
        peers = list(_discover_peers(paths, state, args).values())
        peers.sort(key=lambda item: (item.get("displayName") or "", item["peerId"]))
        if args.record and peers:
            for peer in peers:
                upsert_peer(
                    paths,
                    state,
                    {
                        "peerId": peer["peerId"],
                        "displayName": peer["displayName"],
                        "nickname": peer["displayName"],
                        "emoji": peer["emoji"],
                        "bio": peer["bio"],
                        "endpoint": peer["endpoint"],
                        "relationshipLevel": state.get("peers", {}).get(peer["peerId"], {}).get("relationshipLevel", "L0"),
                        "status": state.get("peers", {}).get(peer["peerId"], {}).get("status", "discovered"),
                        "lastSeenAt": utc_now(),
                    },
                )
            log_event(paths, "discover.recorded", {"count": len(peers), "peerIds": [peer["peerId"] for peer in peers]})
            save_state(paths, state)
            state = load_state(paths)
            for peer in peers:
                existing = state.get("peers", {}).get(peer["peerId"], {})
                peer["recordedStatus"] = existing.get("status", "unknown")
                peer["recordedLevel"] = existing.get("relationshipLevel", "L0")

        print(_render_discover_table(peers), flush=True)
        if not peers:
            action = input("> ").strip().lower()
            if action in {"q", "quit", "exit"}:
                return
            continue

        raw = input("> ").strip()
        if not raw:
            continue
        if raw.lower() in {"q", "quit", "exit"}:
            return
        if raw.lower() in {"r", "rescan", "刷新"}:
            continue

        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            print("无效操作，请输入例如: p 1 / c 1 / i 1 / m 1 / r / q", flush=True)
            continue
        action, index_str = parts[0].lower(), parts[1]
        try:
            peer = peers[int(index_str) - 1]
        except (ValueError, IndexError):
            print("无效序号", flush=True)
            continue

        if action == "c":
            if _copy_to_clipboard(peer["invite"]):
                print(f"已复制邀请码: {peer['peerId']}", flush=True)
            else:
                print(peer["invite"], flush=True)
            continue
        if action == "i":
            print(peer["invite"], flush=True)
            continue
        if action == "m":
            print(peer["pairCommand"], flush=True)
            print(peer["pairWithInviteCommand"], flush=True)
            continue
        if action == "p":
            try:
                _pair_direct(paths.workspace_root, peer["peerId"], ttl=10)
            except SystemExit as exc:
                print(str(exc), flush=True)
                continue
            _open_chat_panel(paths, peer["peerId"])
            continue
        print("未知操作", flush=True)


def _get_peer_or_die(state: dict, peer_id: str, require_known: bool = False) -> dict:
    peer = state.get("peers", {}).get(peer_id)
    if not peer:
        raise SystemExit(f"Unknown peer: {peer_id}")
    if not require_known and peer.get("status") != "active":
        raise SystemExit(f"Peer {peer_id} is discovered but not paired yet")
    return peer


def cmd_peers(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    print(json.dumps(list(state.get("peers", {}).values()), ensure_ascii=False, indent=2))


def _send_chat_message(args: argparse.Namespace) -> dict:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_peer_or_die(state, args.peer_id)
    envelope = make_envelope(state["identity"]["id"], "chat.message", {"message": args.message})
    request_json(f"{peer['endpoint']}/clawsoc/message", envelope)
    peer["lastMessageAt"] = utc_now()
    peer["lastSeenAt"] = utc_now()
    log_message(paths, peer["peerId"], "outbound", args.message, envelope["requestId"], {"type": "chat"})
    log_event(paths, "message.sent", {"peerId": peer["peerId"], "requestId": envelope["requestId"]})
    save_state(paths, state)
    return {"ok": True, "peerId": peer["peerId"]}


def cmd_chat(args: argparse.Namespace) -> None:
    print(json.dumps(_send_chat_message(args), ensure_ascii=False, indent=2))


def cmd_history(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    peer = _get_peer_or_die(state, args.peer_id)
    history_path = paths.peers_dir / peer["peerId"] / "messages.jsonl"
    print(render_history(load_history(history_path), limit=args.limit))


def cmd_share(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_peer_or_die(state, args.peer_id)
    share_type = SHARE_TYPE_ALIASES.get(args.share_type, args.share_type)
    if share_type not in SHARE_MIN_LEVEL:
        raise SystemExit(f"Unknown share type: {share_type}")
    min_level = SHARE_MIN_LEVEL[share_type]
    if not level_at_least(peer["relationshipLevel"], min_level):
        raise SystemExit(
            f"Share type {share_type} requires {min_level}, current relationship is {peer['relationshipLevel']}"
        )
    content = build_share_content(
        share_type,
        workspace_root=paths.workspace_root,
        identity=state["identity"],
        extra_keywords=state.get("settings", {}).get("redactionKeywords", []),
    )
    content = sanitize_share_content(content, state.get("settings", {}).get("redactionKeywords", []))
    envelope = make_envelope(state["identity"]["id"], "share.send", {"item": args.item})
    envelope["shareType"] = share_type
    envelope["relationshipLevel"] = peer["relationshipLevel"]
    envelope["redacted"] = True
    envelope["content"] = content
    request_json(f"{peer['endpoint']}/clawsoc/share", envelope)
    peer["lastSharedAt"] = utc_now()
    peer["lastSeenAt"] = utc_now()
    log_share(paths, peer["peerId"], share_type, envelope, envelope["requestId"])
    log_event(paths, "share.sent", {"peerId": peer["peerId"], "shareType": share_type, "requestId": envelope["requestId"]})
    save_state(paths, state)
    print(json.dumps({"ok": True, "peerId": peer["peerId"], "shareType": share_type}, ensure_ascii=False, indent=2))


def cmd_network(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    identity = state.get("identity", {})
    center = identity.get("displayName") or identity.get("id") or "self"
    lines = [f"{center} ({identity.get('id', '-')})"]
    peers = sorted(state.get("peers", {}).values(), key=lambda item: item.get("relationshipLevel", "L0"))
    if not peers:
        lines.append("└─ 暂无已连接 Claw")
        print("\n".join(lines))
        return
    for idx, peer in enumerate(peers):
        branch = "└─" if idx == len(peers) - 1 else "├─"
        level = peer.get("relationshipLevel", "L0")
        label = LEVEL_NAMES.get(level, level)
        lines.append(
            f"{branch} {peer.get('displayName') or peer['peerId']} [{peer['peerId']}] {level} {label} ({peer.get('status', 'unknown')}) @ {peer.get('endpoint', '-')}"
        )
    print("\n".join(lines))


def cmd_relationship(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    subcommand = args.relationship_command
    if subcommand == "list":
        print(json.dumps({"peers": list(state.get("peers", {}).values()), "pending": state.get("pending", {})}, ensure_ascii=False, indent=2))
        return

    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_peer_or_die(state, args.peer_id)

    if subcommand == "upgrade":
        current_index = ["L0", "L1", "L2", "L3", "L4"].index(peer["relationshipLevel"])
        target_level = args.level or (["L0", "L1", "L2", "L3", "L4"][min(current_index + 1, 4)])
        target_level = normalize_level(target_level)
        envelope = make_envelope(state["identity"]["id"], "relationship.upgrade", {"targetLevel": target_level})
        response = request_json(f"{peer['endpoint']}/clawsoc/relationship/upgrade", envelope)
        outbound = {
            "requestId": envelope["requestId"],
            "fromPeerId": state["identity"]["id"],
            "toPeerId": peer["peerId"],
            "targetLevel": target_level,
            "createdAt": utc_now(),
            "status": "pending-outbound",
        }
        state["pending"]["upgradeRequests"] = [
            item for item in state["pending"]["upgradeRequests"] if item.get("toPeerId") != peer["peerId"]
        ]
        state["pending"]["upgradeRequests"].append(outbound)
        save_state(paths, state)
        print(json.dumps({"ok": True, "request": response["request"]}, ensure_ascii=False, indent=2))
        return

    if subcommand == "accept-upgrade":
        request = None
        for item in state["pending"]["upgradeRequests"]:
            if item["fromPeerId"] == peer["peerId"] and item["status"] == "pending-inbound":
                request = item
                break
        if not request:
            raise SystemExit(f"No pending inbound upgrade request from {peer['peerId']}")
        target_level = normalize_level(request["targetLevel"])
        peer["relationshipLevel"] = target_level
        peer["updatedAt"] = utc_now()
        envelope = make_envelope(
            state["identity"]["id"],
            "relationship.accept",
            {"requestId": request["requestId"], "targetLevel": target_level},
        )
        request_json(f"{peer['endpoint']}/clawsoc/relationship/accept", envelope)
        state["pending"]["upgradeRequests"] = [
            item for item in state["pending"]["upgradeRequests"] if item["requestId"] != request["requestId"]
        ]
        save_state(paths, state)
        print(json.dumps({"ok": True, "peer": peer}, ensure_ascii=False, indent=2))
        return

    if subcommand == "downgrade":
        target_level = normalize_level(args.level)
        peer["relationshipLevel"] = target_level
        peer["updatedAt"] = utc_now()
        log_event(paths, "relationship.downgraded", {"peerId": peer["peerId"], "targetLevel": target_level})
        save_state(paths, state)
        print(json.dumps({"ok": True, "peer": peer}, ensure_ascii=False, indent=2))
        return

    raise SystemExit(f"Unknown relationship subcommand: {subcommand}")


def cmd_serve(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = init_state(paths, host=args.host, port=args.port)
    app = {"paths": paths, "share_requirements": SHARE_MIN_LEVEL}
    serve(app, args.host, args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClawSoc LAN MVP CLI")
    parser.add_argument("--workspace-root", help="Override workspace root")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", aliases=["初始化"])
    init_parser.add_argument("--host", default="0.0.0.0")
    init_parser.add_argument("--port", type=int, default=45678)
    init_parser.add_argument("--force", action="store_true")
    init_parser.set_defaults(func=cmd_init)

    identity_parser = sub.add_parser("identity", aliases=["身份"])
    identity_parser.set_defaults(func=cmd_identity)

    invite_parser = sub.add_parser("invite", aliases=["邀请"])
    invite_parser.add_argument("--ttl", type=int, default=10)
    invite_parser.set_defaults(func=cmd_invite)

    discover_parser = sub.add_parser("discover", aliases=["发现"])
    discover_parser.add_argument("--cidr", help="CIDR to scan, e.g. 192.168.1.0/24")
    discover_parser.add_argument("--hosts", help="Comma-separated host list to scan")
    discover_parser.add_argument("--ports", default="45678", help="Comma-separated port list")
    discover_parser.add_argument("--workers", type=int, default=32)
    discover_parser.add_argument("--timeout", type=float, default=0.6)
    discover_parser.add_argument("--record", action="store_true", help="Record discovered peers into state")
    discover_parser.set_defaults(func=cmd_discover)

    discover_ui_parser = sub.add_parser("discover-ui", aliases=["发现页", "发现面板"])
    discover_ui_parser.add_argument("--cidr", help="CIDR to scan, e.g. 192.168.1.0/24")
    discover_ui_parser.add_argument("--hosts", help="Comma-separated host list to scan")
    discover_ui_parser.add_argument("--ports", default="45678", help="Comma-separated port list")
    discover_ui_parser.add_argument("--workers", type=int, default=32)
    discover_ui_parser.add_argument("--timeout", type=float, default=0.6)
    discover_ui_parser.add_argument("--record", action="store_true", default=True, help="Record discovered peers into state")
    discover_ui_parser.set_defaults(func=cmd_discover_ui)

    pair_parser = sub.add_parser("pair", aliases=["配对"])
    pair_parser.add_argument("invite")
    pair_parser.set_defaults(func=cmd_pair)

    pair_direct_parser = sub.add_parser("pair-direct", aliases=["一键配对", "快速配对"])
    pair_direct_parser.add_argument("peer_id")
    pair_direct_parser.add_argument("--ttl", type=int, default=10)
    pair_direct_parser.set_defaults(func=cmd_pair_direct)

    peers_parser = sub.add_parser("peers", aliases=["对等体", "伙伴"])
    peers_parser.set_defaults(func=cmd_peers)

    chat_parser = sub.add_parser("chat", aliases=["聊天"])
    chat_parser.add_argument("peer_id")
    chat_parser.add_argument("message")
    chat_parser.set_defaults(func=cmd_chat)

    history_parser = sub.add_parser("history", aliases=["历史"])
    history_parser.add_argument("peer_id")
    history_parser.add_argument("--limit", type=int, default=20)
    history_parser.set_defaults(func=cmd_history)

    share_parser = sub.add_parser("share", aliases=["分享"])
    share_parser.add_argument("share_type")
    share_parser.add_argument("peer_id")
    share_parser.add_argument("item", nargs="?", default="")
    share_parser.set_defaults(func=cmd_share)

    relationship_parser = sub.add_parser("relationship", aliases=["关系"])
    relationship_sub = relationship_parser.add_subparsers(dest="relationship_command", required=True)
    rel_list = relationship_sub.add_parser("list", aliases=["列表"])
    rel_list.set_defaults(func=cmd_relationship)
    rel_up = relationship_sub.add_parser("upgrade", aliases=["升级"])
    rel_up.add_argument("peer_id")
    rel_up.add_argument("level", nargs="?")
    rel_up.set_defaults(func=cmd_relationship)
    rel_accept = relationship_sub.add_parser("accept-upgrade", aliases=["接受升级"])
    rel_accept.add_argument("peer_id")
    rel_accept.set_defaults(func=cmd_relationship)
    rel_down = relationship_sub.add_parser("downgrade", aliases=["降级"])
    rel_down.add_argument("peer_id")
    rel_down.add_argument("level")
    rel_down.set_defaults(func=cmd_relationship)

    serve_parser = sub.add_parser("serve", aliases=["服务", "监听"])
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=45678)
    serve_parser.set_defaults(func=cmd_serve)

    network_parser = sub.add_parser("network", aliases=["网络"])
    network_parser.set_defaults(func=cmd_network)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
