#!/usr/bin/env python3
from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from soc_store import (
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


class ClawSocHandler(BaseHTTPRequestHandler):
    server_version = "ClawSoc/0.1"

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parse_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Missing request body")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    @property
    def app(self):
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/clawsoc/health":
            state = load_state(self.app["paths"])
            self._send(
                200,
                {
                    "ok": True,
                    "peerId": state.get("identity", {}).get("id"),
                    "displayName": state.get("identity", {}).get("displayName"),
                    "emoji": state.get("identity", {}).get("emoji"),
                    "bio": state.get("identity", {}).get("bio"),
                    "endpoint": state.get("identity", {}).get("endpoint"),
                    "port": state.get("settings", {}).get("port"),
                },
            )
            return
        self._send(404, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            payload = self._parse_json()
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return

        route = urlparse(self.path).path
        try:
            if route == "/clawsoc/pair":
                self._handle_pair(payload)
            elif route == "/clawsoc/message":
                self._handle_message(payload)
            elif route == "/clawsoc/share":
                self._handle_share(payload)
            elif route == "/clawsoc/relationship/upgrade":
                self._handle_upgrade(payload)
            elif route == "/clawsoc/relationship/accept":
                self._handle_upgrade_accept(payload)
            else:
                self._send(404, {"error": "Not found"})
        except SystemExit as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc)})

    def _handle_pair(self, data: dict) -> None:
        state = load_state(self.app["paths"])
        peer_id = data["fromPeerId"]
        existing = state["peers"].get(peer_id, {})
        peer = upsert_peer(
            self.app["paths"],
            state,
            {
                "peerId": peer_id,
                "displayName": data["payload"]["displayName"],
                "nickname": data["payload"]["displayName"],
                "emoji": data["payload"].get("emoji"),
                "bio": data["payload"].get("bio"),
                "endpoint": data["payload"]["endpoint"],
                "relationshipLevel": existing.get("relationshipLevel", "L0"),
                "status": "active",
                "lastSeenAt": utc_now(),
            },
        )
        state["audit"]["lastSeenAt"] = utc_now()
        log_event(self.app["paths"], "pair.accepted", {"peerId": peer_id, "requestId": data["requestId"]})
        save_state(self.app["paths"], state)
        self._send(
            200,
            {
                "ok": True,
                "peer": peer,
                "identity": {
                    "peerId": state["identity"]["id"],
                    "displayName": state["identity"]["displayName"],
                    "emoji": state["identity"].get("emoji"),
                    "bio": state["identity"].get("bio"),
                    "endpoint": state["identity"]["endpoint"],
                },
            },
        )

    def _handle_message(self, data: dict) -> None:
        state = load_state(self.app["paths"])
        peer_id = data["fromPeerId"]
        peer = state["peers"].get(peer_id)
        if not peer:
            raise SystemExit(f"Unknown peer: {peer_id}")
        message = data["payload"]["message"]
        peer["lastSeenAt"] = utc_now()
        peer["lastMessageAt"] = utc_now()
        state["audit"]["lastMessageAt"] = peer["lastMessageAt"]
        log_message(self.app["paths"], peer_id, "inbound", message, data["requestId"], {"type": "chat"})
        log_event(self.app["paths"], "message.received", {"peerId": peer_id, "requestId": data["requestId"]})
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True})

    def _handle_share(self, data: dict) -> None:
        state = load_state(self.app["paths"])
        peer_id = data["fromPeerId"]
        peer = state["peers"].get(peer_id)
        if not peer:
            raise SystemExit(f"Unknown peer: {peer_id}")
        share_type = data["shareType"]
        if share_type not in self.app["share_requirements"]:
            raise SystemExit(f"Unknown share type: {share_type}")
        min_level = self.app["share_requirements"][share_type]
        if not level_at_least(peer["relationshipLevel"], min_level):
            raise SystemExit(
                f"Share type {share_type} requires {min_level}, current level is {peer['relationshipLevel']}"
            )
        peer["lastSeenAt"] = utc_now()
        peer["lastSharedAt"] = utc_now()
        state["audit"]["lastSharedAt"] = peer["lastSharedAt"]
        log_share(self.app["paths"], peer_id, share_type, data, data["requestId"])
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True})

    def _handle_upgrade(self, data: dict) -> None:
        state = load_state(self.app["paths"])
        peer_id = data["fromPeerId"]
        peer = state["peers"].get(peer_id)
        if not peer:
            raise SystemExit(f"Unknown peer: {peer_id}")
        target_level = normalize_level(data["payload"]["targetLevel"])
        request = {
            "requestId": data["requestId"],
            "fromPeerId": peer_id,
            "targetLevel": target_level,
            "createdAt": utc_now(),
            "status": "pending-inbound",
        }
        state["pending"]["upgradeRequests"] = [
            item
            for item in state["pending"]["upgradeRequests"]
            if not (item["fromPeerId"] == peer_id and item["status"].startswith("pending"))
        ]
        state["pending"]["upgradeRequests"].append(request)
        log_event(self.app["paths"], "relationship.upgrade.requested", request)
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True, "request": request})

    def _handle_upgrade_accept(self, data: dict) -> None:
        state = load_state(self.app["paths"])
        peer_id = data["fromPeerId"]
        peer = state["peers"].get(peer_id)
        if not peer:
            raise SystemExit(f"Unknown peer: {peer_id}")
        target_level = normalize_level(data["payload"]["targetLevel"])
        peer["relationshipLevel"] = target_level
        peer["updatedAt"] = utc_now()
        pending = []
        for item in state["pending"]["upgradeRequests"]:
            if item["requestId"] == data["payload"]["requestId"]:
                item["status"] = "accepted"
            else:
                pending.append(item)
        state["pending"]["upgradeRequests"] = pending
        log_event(
            self.app["paths"],
            "relationship.upgrade.accepted",
            {"peerId": peer_id, "requestId": data["payload"]["requestId"], "targetLevel": target_level},
        )
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True, "peer": peer})


def serve(app: dict, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ClawSocHandler)
    server.app = app  # type: ignore[attr-defined]
    print(f"ClawSoc server listening on http://{host}:{port}", flush=True)
    server.serve_forever()
