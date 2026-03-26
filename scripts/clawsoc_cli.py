#!/usr/bin/env python3
"""ClawSoc CLI — simplified pairing model (v2).

Pairing flow:
  1. Both sides: init + serve
  2. Either side: discover (optional) + pair <endpoint-or-peer-id>
  3. Done. Both sides are L0 peers.

No invite codes. No intermediate steps.
"""
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
from sharing import allowed_share_types, build_share_content, sanitize_share_content
from soc_store import (
    LEVEL_NAMES,
    SHARE_MIN_LEVEL,
    get_paths,
    init_state,
    level_strictly_higher,
    level_at_least,
    load_state,
    log_event,
    log_message,
    log_share,
    normalize_level,
    save_state,
    upsert_peer,
    ui_urls_from_state,
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

SERVICE_TRIGGER_PHRASES = {
    "启动",
    "启动服务",
    "启动监听",
    "启动clawsoc",
    "启动clawsoc服务",
    "启动claw社交",
    "启动社交",
    "开始服务",
    "开始监听",
    "开始clawsoc",
    "开始clawsoc服务",
    "开始社交",
    "开启服务",
    "开启监听",
    "开启clawsoc",
    "开启clawsoc服务",
    "开启社交",
    "上线",
    "上线社交",
    "社交上线",
    "进入监听",
    "开始待机",
    "进入待机",
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# init / identity / serve
# ---------------------------------------------------------------------------

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


def _render_serve_panel(state: dict) -> str:
    identity = state.get("identity", {})
    endpoint = identity.get("endpoint", "-")
    health_url = f"{endpoint}/clawsoc/health" if endpoint and endpoint != "-" else "-"
    ui_urls = ui_urls_from_state(state)
    lines = [
        "",
        "ClawSoc 服务已启动",
        "-" * 72,
        f"名称: {identity.get('displayName', '-')}",
        f"ID:   {identity.get('id', '-')}",
        f"简介: {identity.get('bio', '-')}",
        f"配对地址: {endpoint}",
        f"健康检查: {health_url}",
        f"Web UI: {ui_urls[0] if ui_urls else '-'}",
        "-" * 72,
        "推荐下一步",
        "1. 另一台 OpenClaw 执行发现：",
        "   python3 scripts/clawsoc_cli.py 发现 --cidr 192.168.1.0/24 --ports 45678 --record",
        "2. 或者直接交互式发现：",
        "   python3 scripts/clawsoc_cli.py 发现页 --cidr 192.168.1.0/24 --ports 45678",
        "3. 如果只想本机演示：",
        "   python3 scripts/clawsoc_cli.py 发现 --hosts 127.0.0.1 --ports 45678 --record",
        "-" * 72,
    ]
    if len(ui_urls) > 1:
        lines.insert(8, f"备用 Web UI: {', '.join(ui_urls[1:])}")
    return "\n".join(lines)


def cmd_serve(args: argparse.Namespace) -> None:
    from peer_server import serve
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = init_state(paths, host=args.host, port=args.port)
    app = {"paths": paths, "share_requirements": SHARE_MIN_LEVEL}
    print(_render_serve_panel(state), flush=True)
    serve(app, args.host, args.port)


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------

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
        }

    discovered: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, args.workers)) as executor:
        for result in executor.map(_probe, targets):
            if result:
                discovered.append(result)

    deduped: dict[str, dict] = {}
    for item in discovered:
        deduped[item["peerId"]] = item

    for peer in deduped.values():
        existing = state.get("peers", {}).get(peer["peerId"], {})
        peer["status"] = existing.get("status", "discovered")
        peer["level"] = existing.get("relationshipLevel", "L0")
    return deduped


def cmd_discover(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    local_hint = _local_service_hint(state)
    if local_hint:
        print(f"提示: {local_hint}", file=sys.stderr)
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


# ---------------------------------------------------------------------------
# pair  (simplified: endpoint or peer_id, no invite codes)
# ---------------------------------------------------------------------------

def _resolve_endpoint(state: dict, target: str) -> str:
    """Resolve target to an endpoint URL.

    target can be:
      - A full URL like http://192.168.1.10:45678
      - A peer_id that was previously discovered/recorded
      - An IP address (port defaults to 45678)
      - host:port
    """
    if target.startswith("http://") or target.startswith("https://"):
        return target.rstrip("/")
    # Known peer_id
    peer = state.get("peers", {}).get(target)
    if peer and peer.get("endpoint"):
        return peer["endpoint"].rstrip("/")
    # host:port
    if ":" in target:
        return f"http://{target}"
    # Bare IP or hostname
    return f"http://{target}:45678"


def _do_pair(paths, state: dict, endpoint: str) -> dict:
    """Pair with a peer at the given endpoint. Returns the peer dict."""
    health = healthcheck(f"{endpoint}/clawsoc/health")
    if not health.get("ok"):
        raise SystemExit(f"Peer at {endpoint} is not healthy: {health}")
    remote_id = health.get("peerId")
    if not remote_id:
        raise SystemExit(f"Peer at {endpoint} returned no peerId")
    if remote_id == state.get("identity", {}).get("id"):
        raise SystemExit("Cannot pair with yourself")

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
    response = request_json(f"{endpoint}/clawsoc/pair", envelope)
    remote_identity = response.get("identity", {})
    existing = state.get("peers", {}).get(remote_id, {})
    peer = upsert_peer(
        paths,
        state,
        {
            "peerId": remote_identity.get("peerId") or remote_id,
            "displayName": remote_identity.get("displayName") or health.get("displayName") or remote_id,
            "nickname": remote_identity.get("displayName") or health.get("displayName") or remote_id,
            "emoji": remote_identity.get("emoji") or health.get("emoji"),
            "bio": remote_identity.get("bio") or health.get("bio"),
            "endpoint": remote_identity.get("endpoint") or endpoint,
            "relationshipLevel": existing.get("relationshipLevel", "L0"),
            "status": "active",
            "lastSeenAt": utc_now(),
        },
    )
    log_event(paths, "pair.completed", {"peerId": peer["peerId"], "endpoint": endpoint, "requestId": envelope["requestId"]})
    save_state(paths, state)
    return peer


def cmd_pair(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    local_hint = _local_service_hint(state)
    if local_hint:
        print(f"提示: {local_hint}", file=sys.stderr)
    endpoint = _resolve_endpoint(state, args.target)
    peer = _do_pair(paths, state, endpoint)
    print(json.dumps({"ok": True, "peer": peer}, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# discover-ui (interactive terminal)
# ---------------------------------------------------------------------------

def _copy_to_clipboard(text: str) -> bool:
    for program in ("pbcopy",):
        try:
            subprocess.run([program], input=text.encode("utf-8"), check=True)
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


def _normalize_natural_argv(argv: list[str]) -> list[str]:
    """Map friendly Chinese phrases to concrete CLI commands.

    This keeps the user-facing experience soft while leaving the real
    subcommands stable and scriptable.
    """
    if not argv:
        return argv

    global_args = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if not token.startswith("--"):
            break
        global_args.append(token)
        if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
            global_args.append(argv[index + 1])
            index += 2
        else:
            index += 1

    phrase_tokens = []
    subcommand_args = []
    saw_option = False
    for token in argv[index:]:
        if token.startswith("--"):
            saw_option = True
        if saw_option:
            subcommand_args.append(token)
        else:
            phrase_tokens.append(token)

    joined = "".join(phrase_tokens).strip().lower()
    if joined in SERVICE_TRIGGER_PHRASES:
        return [*global_args, "serve", *subcommand_args]
    return argv


def _local_service_hint(state: dict) -> str | None:
    identity = state.get("identity", {})
    endpoint = identity.get("endpoint")
    if not endpoint:
        return "还没有本机 endpoint，建议先执行: python3 scripts/clawsoc_cli.py 启动 ClawSoc 服务"
    payload = probe_health(f"{endpoint}/clawsoc/health", timeout=0.4)
    if payload and payload.get("ok"):
        return None
    return (
        "本机 ClawSoc 服务似乎还没启动。建议先执行: "
        "python3 scripts/clawsoc_cli.py 启动 ClawSoc 服务"
    )


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
    return [st for st, ml in SHARE_MIN_LEVEL.items() if level_at_least(level, ml)]


def _render_chat_panel(paths, peer_id: str, limit: int = 12) -> str:
    state = load_state(paths)
    peer = state.get("peers", {}).get(peer_id)
    if not peer:
        return f"未知 peer: {peer_id}"
    history_path = paths.peers_dir / peer["peerId"] / "messages.jsonl"
    history = render_history(load_history(history_path), limit=limit)
    level = peer.get("relationshipLevel", "L0")
    level_name = LEVEL_NAMES.get(level, level)
    shares = _recent_share_summaries(paths, peer["peerId"])
    quick_shares = ", ".join(_available_quick_shares(level)) or "无"
    title = f"\n聊天页 · {peer.get('displayName') or peer['peerId']} [{peer['peerId']}]"
    share_lines = shares if shares else ["暂无最近分享"]
    return "\n".join([
        title,
        "-" * 72,
        f"关系等级: {level} {level_name}    状态: {peer.get('status', 'unknown')}",
        f"快捷分享: {quick_shares}",
        "最近分享:",
        *(f"- {item}" for item in share_lines),
        "-" * 72,
        history,
        "-" * 72,
        "输入消息直接发送；/share <类型> 快捷分享；/r 刷新；/q 退出",
    ])


def _open_chat_panel(paths, peer_id: str) -> None:
    while True:
        print(_render_chat_panel(paths, peer_id), flush=True)
        try:
            raw = input("chat> ").rstrip("\n")
        except (EOFError, KeyboardInterrupt):
            return
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
        status = peer.get("status", "discovered")
        level = peer.get("level", "L0")
        lines.append(
            f"[{idx}] {peer['displayName']} [{peer['peerId']}]  {status}/{level}  {peer['endpoint']}"
        )
        if peer.get("bio"):
            lines.append(f"     简介: {peer['bio']}")
    lines.extend([
        "-" * 72,
        "操作: p <序号>=配对并聊天  e <序号>=复制endpoint  r=重新扫描  q=退出",
        "自然输入: 配对 1 / 连接 1 / 聊天 1 / endpoint 1 / 复制 1 / 刷新 / 退出",
    ])
    return "\n".join(lines)


def cmd_discover_ui(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    local_hint = _local_service_hint(state)
    if local_hint:
        print(f"提示: {local_hint}", flush=True)

    while True:
        state = load_state(paths)
        peers = list(_discover_peers(paths, state, args).values())
        peers.sort(key=lambda item: (item.get("displayName") or "", item["peerId"]))

        if peers:
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
            log_event(paths, "discover.recorded", {"count": len(peers), "peerIds": [p["peerId"] for p in peers]})
            save_state(paths, state)
            state = load_state(paths)
            for peer in peers:
                existing = state.get("peers", {}).get(peer["peerId"], {})
                peer["status"] = existing.get("status", "discovered")
                peer["level"] = existing.get("relationshipLevel", "L0")

        print(_render_discover_table(peers), flush=True)
        if not peers:
            try:
                action = input("> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if action in {"q", "quit", "exit"}:
                return
            continue

        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not raw:
            continue
        if raw.lower() in {"q", "quit", "exit"}:
            return
        if raw.lower() in {"r", "rescan", "刷新"}:
            continue

        normalized = raw.lower()
        alias_map = {
            "配对": "p",
            "连接": "p",
            "连": "p",
            "聊天": "p",
            "进入": "p",
            "endpoint": "e",
            "复制": "e",
            "地址": "e",
            "刷新": "r",
            "重扫": "r",
            "退出": "q",
        }
        parts = raw.split(maxsplit=1)
        if len(parts) == 2 and parts[0] in alias_map:
            parts = [alias_map[parts[0]], parts[1]]
        elif normalized in alias_map:
            parts = [alias_map[normalized]]

        if len(parts) == 1:
            action = parts[0].lower()
            if action in {"q", "quit", "exit"}:
                return
            if action in {"r", "rescan", "刷新"}:
                continue
            print("无效操作，请输入: p <序号> / e <序号> / r / q", flush=True)
            continue

        if len(parts) != 2:
            print("无效操作，请输入: p <序号> / e <序号> / r / q", flush=True)
            continue
        action, index_str = parts[0].lower(), parts[1]
        try:
            peer = peers[int(index_str) - 1]
        except (ValueError, IndexError):
            print("无效序号", flush=True)
            continue

        if action == "e":
            text = peer["endpoint"]
            if _copy_to_clipboard(text):
                print(f"已复制: {text}", flush=True)
            else:
                print(text, flush=True)
            continue
        if action == "p":
            try:
                _do_pair(paths, state, peer["endpoint"])
                print(f"✅ 已配对: {peer['displayName']} [{peer['peerId']}]", flush=True)
            except SystemExit as exc:
                print(str(exc), flush=True)
                continue
            _open_chat_panel(paths, peer["peerId"])
            continue
        print("未知操作", flush=True)


# ---------------------------------------------------------------------------
# chat / history
# ---------------------------------------------------------------------------

def _get_active_peer(state: dict, peer_id: str) -> dict:
    peer = state.get("peers", {}).get(peer_id)
    if not peer:
        raise SystemExit(f"Unknown peer: {peer_id}. Run 'discover --record' or 'pair' first.")
    if peer.get("status") != "active":
        raise SystemExit(f"Peer {peer_id} not paired. Run: pair {peer.get('endpoint') or peer_id}")
    return peer


def _send_chat_message(args: argparse.Namespace) -> dict:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_active_peer(state, args.peer_id)
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
    peer = _get_active_peer(state, args.peer_id)
    history_path = paths.peers_dir / peer["peerId"] / "messages.jsonl"
    print(render_history(load_history(history_path), limit=args.limit))


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------

def cmd_share(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_active_peer(state, args.peer_id)
    share_type = SHARE_TYPE_ALIASES.get(args.share_type, args.share_type)
    if share_type not in SHARE_MIN_LEVEL:
        raise SystemExit(f"Unknown share type: {share_type}. Available: {', '.join(sorted(SHARE_MIN_LEVEL))}")
    min_level = SHARE_MIN_LEVEL[share_type]
    if not level_at_least(peer["relationshipLevel"], min_level):
        raise SystemExit(
            f"Share type '{share_type}' requires {min_level}, current is {peer['relationshipLevel']}"
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


# ---------------------------------------------------------------------------
# relationship / network / peers
# ---------------------------------------------------------------------------

def cmd_peers(args: argparse.Namespace) -> None:
    paths = get_paths(Path(args.workspace_root) if args.workspace_root else None)
    state = load_state(paths)
    print(json.dumps(list(state.get("peers", {}).values()), ensure_ascii=False, indent=2))


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

    if subcommand in ("list", "列表"):
        print(json.dumps({"peers": list(state.get("peers", {}).values()), "pending": state.get("pending", {})}, ensure_ascii=False, indent=2))
        return

    if not state.get("identity"):
        state = init_state(paths)
    peer = _get_active_peer(state, args.peer_id)

    if subcommand in ("upgrade", "升级"):
        current_index = ["L0", "L1", "L2", "L3", "L4"].index(peer["relationshipLevel"])
        target_level = args.level or (["L0", "L1", "L2", "L3", "L4"][min(current_index + 1, 4)])
        target_level = normalize_level(target_level)
        if not level_strictly_higher(target_level, peer["relationshipLevel"]):
            raise SystemExit(
                f"Relationship upgrade must be higher than current level {peer['relationshipLevel']}, got {target_level}"
            )
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
        print(json.dumps({"ok": True, "request": response.get("request", outbound)}, ensure_ascii=False, indent=2))
        return

    if subcommand in ("accept-upgrade", "接受升级"):
        request = None
        for item in state["pending"]["upgradeRequests"]:
            if item["fromPeerId"] == peer["peerId"] and item["status"] == "pending-inbound":
                request = item
                break
        if not request:
            raise SystemExit(f"No pending upgrade request from {peer['peerId']}")
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

    if subcommand in ("downgrade", "降级"):
        target_level = normalize_level(args.level)
        peer["relationshipLevel"] = target_level
        peer["updatedAt"] = utc_now()
        log_event(paths, "relationship.downgraded", {"peerId": peer["peerId"], "targetLevel": target_level})
        save_state(paths, state)
        print(json.dumps({"ok": True, "peer": peer}, ensure_ascii=False, indent=2))
        return

    raise SystemExit(f"Unknown relationship subcommand: {subcommand}")


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ClawSoc CLI v2 — simplified pairing")
    parser.add_argument("--workspace-root", help="Override workspace root")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser("init", aliases=["初始化"])
    init_p.add_argument("--host", default="0.0.0.0")
    init_p.add_argument("--port", type=int, default=45678)
    init_p.add_argument("--force", action="store_true")
    init_p.set_defaults(func=cmd_init)

    # identity
    id_p = sub.add_parser("identity", aliases=["身份"])
    id_p.set_defaults(func=cmd_identity)

    # discover
    disc_p = sub.add_parser("discover", aliases=["发现"])
    disc_p.add_argument("--cidr", help="CIDR to scan, e.g. 192.168.1.0/24")
    disc_p.add_argument("--hosts", help="Comma-separated host list")
    disc_p.add_argument("--ports", default="45678", help="Comma-separated port list")
    disc_p.add_argument("--workers", type=int, default=32)
    disc_p.add_argument("--timeout", type=float, default=0.6)
    disc_p.add_argument("--record", action="store_true", help="Save discovered peers to state")
    disc_p.set_defaults(func=cmd_discover)

    # discover-ui
    dui_p = sub.add_parser("discover-ui", aliases=["发现页"])
    dui_p.add_argument("--cidr", help="CIDR to scan")
    dui_p.add_argument("--hosts", help="Comma-separated host list")
    dui_p.add_argument("--ports", default="45678")
    dui_p.add_argument("--workers", type=int, default=32)
    dui_p.add_argument("--timeout", type=float, default=0.6)
    dui_p.set_defaults(func=cmd_discover_ui)

    # pair (simplified: takes endpoint, peer_id, or IP)
    pair_p = sub.add_parser("pair", aliases=["配对"])
    pair_p.add_argument("target", help="Endpoint URL, peer_id, or IP address")
    pair_p.set_defaults(func=cmd_pair)

    # peers
    peers_p = sub.add_parser("peers", aliases=["伙伴"])
    peers_p.set_defaults(func=cmd_peers)

    # chat
    chat_p = sub.add_parser("chat", aliases=["聊天"])
    chat_p.add_argument("peer_id")
    chat_p.add_argument("message")
    chat_p.set_defaults(func=cmd_chat)

    # history
    hist_p = sub.add_parser("history", aliases=["历史"])
    hist_p.add_argument("peer_id")
    hist_p.add_argument("--limit", type=int, default=20)
    hist_p.set_defaults(func=cmd_history)

    # share
    share_p = sub.add_parser("share", aliases=["分享"])
    share_p.add_argument("share_type")
    share_p.add_argument("peer_id")
    share_p.add_argument("item", nargs="?", default="")
    share_p.set_defaults(func=cmd_share)

    # relationship
    rel_p = sub.add_parser("relationship", aliases=["关系"])
    rel_sub = rel_p.add_subparsers(dest="relationship_command", required=True)

    rel_sub.add_parser("list", aliases=["列表"]).set_defaults(func=cmd_relationship)

    rel_up = rel_sub.add_parser("upgrade", aliases=["升级"])
    rel_up.add_argument("peer_id")
    rel_up.add_argument("level", nargs="?")
    rel_up.set_defaults(func=cmd_relationship)

    rel_acc = rel_sub.add_parser("accept-upgrade", aliases=["接受升级"])
    rel_acc.add_argument("peer_id")
    rel_acc.set_defaults(func=cmd_relationship)

    rel_down = rel_sub.add_parser("downgrade", aliases=["降级"])
    rel_down.add_argument("peer_id")
    rel_down.add_argument("level")
    rel_down.set_defaults(func=cmd_relationship)

    # serve
    serve_p = sub.add_parser("serve", aliases=["服务", "监听"])
    serve_p.add_argument("--host", default="0.0.0.0")
    serve_p.add_argument("--port", type=int, default=45678)
    serve_p.set_defaults(func=cmd_serve)

    # network
    net_p = sub.add_parser("network", aliases=["网络"])
    net_p.set_defaults(func=cmd_network)

    return parser


def main() -> None:
    parser = build_parser()
    argv = _normalize_natural_argv(sys.argv[1:])
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
