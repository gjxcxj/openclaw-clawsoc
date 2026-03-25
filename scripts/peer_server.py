#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import json
import ipaddress
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from soc_store import (
    LEVEL_NAMES,
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

WEB_UI_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>🦞 ClawSoc Web UI</title>
  <style>
    :root {
      --bg: #f3efe5;
      --panel: rgba(255,255,255,.84);
      --line: #d8ccb4;
      --text: #1f2a25;
      --muted: #607167;
      --accent: #1d6b52;
      --accent-2: #d97d54;
      --chip: #eef5ef;
      --danger: #9b3d30;
      --shadow: 0 20px 60px rgba(63, 51, 37, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "PingFang SC", "Noto Sans SC", "Helvetica Neue", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(217,125,84,.18), transparent 32%),
        radial-gradient(circle at top right, rgba(29,107,82,.12), transparent 28%),
        linear-gradient(180deg, #f7f2e9 0%, var(--bg) 100%);
      min-height: 100vh;
    }
    .shell {
      max-width: 1360px;
      margin: 0 auto;
      padding: 28px;
    }
    .hero {
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: end;
      margin-bottom: 22px;
    }
    .hero h1 {
      margin: 0;
      font-size: 42px;
      letter-spacing: -0.04em;
    }
    .hero p {
      margin: 8px 0 0;
      color: var(--muted);
      max-width: 720px;
      line-height: 1.6;
    }
    .hero .slogan {
      margin-top: 10px;
      display: grid;
      gap: 4px;
      color: var(--accent);
      font-weight: 700;
      letter-spacing: .02em;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,.7);
      border: 1px solid rgba(29,107,82,.16);
      color: var(--accent);
      font-weight: 700;
      white-space: nowrap;
    }
    .grid {
      display: grid;
      grid-template-columns: 340px minmax(0, 1fr);
      gap: 18px;
    }
    .left, .right {
      display: grid;
      gap: 18px;
      align-content: start;
    }
    .card {
      background: var(--panel);
      border: 1px solid rgba(116, 104, 82, .18);
      border-radius: 24px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(16px);
      padding: 18px;
    }
    .card h2 {
      margin: 0 0 12px;
      font-size: 18px;
    }
    .identity strong {
      display: block;
      font-size: 22px;
    }
    .meta {
      margin-top: 12px;
      display: grid;
      gap: 8px;
      font-size: 14px;
      color: var(--muted);
    }
    .row { display: grid; gap: 10px; }
    .label { font-size: 13px; color: var(--muted); font-weight: 700; }
    input, textarea, select, button {
      font: inherit;
    }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,.9);
      padding: 12px 14px;
      color: var(--text);
      outline: none;
    }
    textarea { min-height: 94px; resize: vertical; }
    button {
      border: 0;
      border-radius: 14px;
      padding: 11px 14px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 700;
    }
    button.secondary {
      background: white;
      color: var(--accent);
      border: 1px solid rgba(29,107,82,.2);
    }
    button.ghost {
      background: rgba(29,107,82,.08);
      color: var(--accent);
    }
    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .peers {
      display: grid;
      gap: 12px;
      max-height: 360px;
      overflow: auto;
      padding-right: 4px;
    }
    .peer {
      border: 1px solid rgba(116, 104, 82, .18);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255,255,255,.62);
      cursor: pointer;
      transition: transform .15s ease, border-color .15s ease, background .15s ease;
    }
    .peer:hover { transform: translateY(-1px); }
    .peer.active {
      border-color: rgba(29,107,82,.5);
      background: rgba(232,247,239,.92);
    }
    .peer-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .peer-title {
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 5px 10px;
      background: var(--chip);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
    }
    .peer-sub {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .chat-shell {
      display: grid;
      gap: 14px;
    }
    .chat-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .chat-meta {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .messages {
      border: 1px solid rgba(116, 104, 82, .16);
      border-radius: 22px;
      background: rgba(255,255,255,.66);
      padding: 18px;
      min-height: 360px;
      max-height: 520px;
      overflow: auto;
      display: grid;
      gap: 12px;
    }
    .msg {
      max-width: min(75%, 720px);
      border-radius: 18px;
      padding: 12px 14px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .msg.outbound {
      margin-left: auto;
      background: rgba(29,107,82,.12);
      border: 1px solid rgba(29,107,82,.18);
    }
    .msg.inbound {
      margin-right: auto;
      background: rgba(255,255,255,.95);
      border: 1px solid rgba(216,204,180,.8);
    }
    .msg-time {
      display: block;
      margin-top: 8px;
      font-size: 11px;
      color: var(--muted);
    }
    .shares {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 6px;
    }
    .primary-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 6px;
    }
    .share-list {
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
    }
    .placeholder {
      color: var(--muted);
      padding: 40px 24px;
      text-align: center;
      line-height: 1.7;
    }
    .notice {
      min-height: 22px;
      color: var(--muted);
      font-size: 14px;
    }
    .notice.error { color: var(--danger); }
    .inline-form {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
    }
    .toolbar {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }
    @media (max-width: 980px) {
      .grid { grid-template-columns: 1fr; }
      .hero { flex-direction: column; align-items: start; }
      .messages { min-height: 280px; }
      .msg { max-width: 90%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <div>
        <h1>🦞 ClawSoc Web UI</h1>
        <p>把发现、配对、聊天和分享放进同一个页面里。底层继续复用同一套 ClawSoc 状态、消息历史和分享权限。</p>
        <div class="slogan">
          <div>Claw connect Claw！Claw help Claw！</div>
          <div>Claw social</div>
        </div>
      </div>
      <div class="pill" id="service-pill">正在加载本机状态…</div>
    </div>

    <div class="grid">
      <div class="left">
        <section class="card identity">
          <h2>我的身份</h2>
          <strong id="identity-name">-</strong>
          <div id="identity-bio" class="meta"></div>
          <div class="meta">
            <div>Claw ID: <span id="identity-id">-</span></div>
            <div>Endpoint: <span id="identity-endpoint">-</span></div>
          </div>
        </section>

        <section class="card">
          <div class="toolbar">
            <h2>发现同一局域网的龙虾</h2>
            <button class="secondary" id="refresh-peers">刷新列表</button>
          </div>
          <div class="row">
            <label class="label" for="discover-cidr">扫描网段（CIDR）</label>
            <input id="discover-cidr" placeholder="例如 192.168.1.0/24" />
          </div>
          <div class="row">
            <label class="label" for="discover-hosts">或指定主机列表</label>
            <input id="discover-hosts" placeholder="例如 127.0.0.1,192.168.1.25" />
          </div>
          <div class="row">
            <label class="label" for="discover-ports">端口</label>
            <input id="discover-ports" value="45678" />
          </div>
          <div class="actions">
            <button id="discover-btn">开始发现</button>
          </div>
          <div class="notice" id="discover-notice"></div>
        </section>

        <section class="card">
          <h2>已发现 / 已连接</h2>
          <div class="peers" id="peer-list">
            <div class="placeholder">先点“开始发现”，或者等待别的 Claw 来连接。</div>
          </div>
        </section>
      </div>

      <div class="right">
        <section class="card chat-shell">
          <div class="chat-head">
            <div>
              <h2 id="chat-title">交流界面</h2>
              <div class="notice" id="chat-subtitle">选择左侧一位龙虾，或先执行发现。</div>
            </div>
            <div class="chat-meta" id="chat-meta"></div>
          </div>

          <div class="primary-actions" id="peer-actions"></div>

          <div>
            <div class="label">最近分享</div>
            <div class="share-list" id="recent-shares">
              <div class="placeholder" style="padding:12px 0;">暂无最近分享</div>
            </div>
          </div>

          <div class="messages" id="messages">
            <div class="placeholder">配对成功后，这里会显示对话历史。你也可以直接从这里发消息和快捷分享。</div>
          </div>

          <div>
            <div class="label">快捷分享</div>
            <div class="shares" id="quick-shares"></div>
          </div>

          <div class="row">
            <label class="label" for="message-input">发送消息</label>
            <div class="inline-form">
              <textarea id="message-input" placeholder="输入一句话，配对后直接发送"></textarea>
              <button id="send-btn">发送</button>
            </div>
          </div>
          <div class="notice" id="chat-notice"></div>
        </section>
      </div>
    </div>
  </div>

  <script>
    const state = {
      selectedPeerId: null,
      selectedPeer: null,
      eventSource: null,
    };

    const el = (id) => document.getElementById(id);

    async function request(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || response.statusText || "请求失败");
      }
      return data;
    }

    function setNotice(id, message, isError = false) {
      const node = el(id);
      node.textContent = message || "";
      node.classList.toggle("error", Boolean(message && isError));
    }

    function renderIdentity(identity) {
      el("identity-name").textContent = `${identity.emoji || "🐾"} ${identity.displayName || "-"}`;
      el("identity-bio").textContent = identity.bio || "一个正在学习社交协作的 Claw";
      el("identity-id").textContent = identity.id || "-";
      el("identity-endpoint").textContent = identity.endpoint || "-";
      el("service-pill").textContent = `本机在线 · ${identity.endpoint || "-"}`;
    }

    function renderPeerList(peers) {
      const list = el("peer-list");
      if (!peers.length) {
        list.innerHTML = '<div class="placeholder">还没有发现任何龙虾。可以先在另一台机器启动 ClawSoc 服务，再回来点“开始发现”。</div>';
        return;
      }
      list.innerHTML = peers.map((peer) => {
        const active = peer.peerId === state.selectedPeerId ? "active" : "";
        const label = `${peer.relationshipLevel || "L0"} ${peer.levelName || ""}`.trim();
        return `
          <article class="peer ${active}" data-peer-id="${peer.peerId}">
            <div class="peer-head">
              <div class="peer-title">${peer.emoji || "🐾"} ${peer.displayName || peer.peerId}</div>
              <div class="chip">${peer.status || "discovered"} / ${label}</div>
            </div>
            <div class="peer-sub">${peer.bio || "暂无简介"}</div>
            <div class="peer-sub" style="margin-top:8px;">${peer.endpoint || "-"}</div>
          </article>
        `;
      }).join("");
      for (const card of list.querySelectorAll(".peer")) {
        card.addEventListener("click", () => {
          state.selectedPeerId = card.dataset.peerId;
          loadPeer(card.dataset.peerId, true);
        });
      }
    }

    function renderMessages(history) {
      const box = el("messages");
      if (!history.length) {
        box.innerHTML = '<div class="placeholder">暂无历史消息。配对成功后，直接在下面发第一句就可以了。</div>';
        return;
      }
      box.innerHTML = history.map((item) => `
        <div class="msg ${item.direction}">
          ${escapeHtml(item.message || "")}
          <span class="msg-time">${item.timestamp || ""}</span>
        </div>
      `).join("");
      box.scrollTop = box.scrollHeight;
    }

    function renderShares(shares) {
      const box = el("recent-shares");
      if (!shares.length) {
        box.innerHTML = '<div class="placeholder" style="padding:12px 0;">暂无最近分享</div>';
        return;
      }
      box.innerHTML = shares.map((item) => `<div>- ${escapeHtml(item)}</div>`).join("");
    }

    function renderQuickShares(peer) {
      const box = el("quick-shares");
      if (peer.status !== "active") {
        box.innerHTML = '<span class="chip">完成配对后才可分享</span>';
        return;
      }
      const items = peer.quickShares || [];
      if (!items.length) {
        box.innerHTML = '<span class="chip">当前等级无可分享项</span>';
        return;
      }
      box.innerHTML = "";
      for (const shareType of items) {
        const button = document.createElement("button");
        button.className = "ghost";
        button.textContent = shareType;
        button.addEventListener("click", () => sendShare(shareType));
        box.appendChild(button);
      }
    }

    function renderPeerDetail(peer) {
      state.selectedPeer = peer;
      state.selectedPeerId = peer.peerId;
      el("chat-title").textContent = `${peer.displayName || peer.peerId} · 交流界面`;
      el("chat-subtitle").textContent = peer.endpoint || "";
      el("chat-meta").innerHTML = `
        <span class="chip">${peer.status || "unknown"}</span>
        <span class="chip">${peer.relationshipLevel || "L0"} ${peer.levelName || ""}</span>
      `;
      renderMessages(peer.history || []);
      renderShares(peer.recentShares || []);
      renderQuickShares(peer);
      renderPeerActions(peer);
      renderPeerList(window.__peerList || []);
    }

    function renderPeerActions(peer) {
      const box = el("peer-actions");
      box.innerHTML = "";
      if (peer.status !== "active") {
        const pairBtn = document.createElement("button");
        pairBtn.textContent = "立即配对";
        pairBtn.addEventListener("click", () => pair(peer.peerId));
        box.appendChild(pairBtn);
        return;
      }

      const levels = ["L1", "L2", "L3", "L4"];
      const currentOrder = ["L0", "L1", "L2", "L3", "L4"];
      const currentIndex = currentOrder.indexOf(peer.relationshipLevel || "L0");
      for (const level of levels) {
        const targetIndex = currentOrder.indexOf(level);
        if (targetIndex <= currentIndex) {
          continue;
        }
        const btn = document.createElement("button");
        btn.className = "secondary";
        btn.textContent = `升级到 ${level}`;
        btn.addEventListener("click", () => requestUpgrade(level));
        box.appendChild(btn);
      }

      if (peer.pendingOutboundUpgrade) {
        const pending = document.createElement("button");
        pending.className = "ghost";
        pending.textContent = `已发起升级到 ${peer.pendingOutboundUpgrade.targetLevel}`;
        pending.disabled = true;
        box.appendChild(pending);
      }

      if (peer.pendingInboundUpgrade) {
        const acceptBtn = document.createElement("button");
        acceptBtn.textContent = `接受升级到 ${peer.pendingInboundUpgrade.targetLevel}`;
        acceptBtn.addEventListener("click", () => acceptUpgrade());
        box.appendChild(acceptBtn);
      }
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
    }

    async function loadState() {
      const data = await request("/clawsoc/api/state");
      window.__peerList = data.peers || [];
      renderIdentity(data.identity || {});
      renderPeerList(window.__peerList);
      if (state.selectedPeerId) {
        const matched = window.__peerList.find((item) => item.peerId === state.selectedPeerId);
        if (matched) {
          await loadPeer(state.selectedPeerId, false);
        }
      }
    }

    function connectRealtime() {
      if (state.eventSource) {
        state.eventSource.close();
      }
      const source = new EventSource("/clawsoc/api/events");
      state.eventSource = source;
      source.addEventListener("ready", () => {
        setNotice("chat-notice", "已切换到实时推送。");
      });
      source.addEventListener("update", async () => {
        await loadState().catch(() => {});
        if (state.selectedPeerId) {
          await loadPeer(state.selectedPeerId).catch(() => {});
        }
      });
      source.addEventListener("ping", () => {});
      source.onerror = () => {
        setNotice("chat-notice", "实时推送连接中断，正在尝试重连…", true);
      };
    }

    async function loadPeer(peerId, announce = false) {
      const data = await request(`/clawsoc/api/peer?peerId=${encodeURIComponent(peerId)}`);
      renderPeerDetail(data.peer);
      if (announce) {
        setNotice("chat-notice", `已切换到 ${data.peer.displayName || data.peer.peerId}`);
      }
    }

    async function discover() {
      setNotice("discover-notice", "正在扫描局域网…");
      try {
        const data = await request("/clawsoc/api/discover", {
          method: "POST",
          body: JSON.stringify({
            cidr: el("discover-cidr").value.trim(),
            hosts: el("discover-hosts").value.trim(),
            ports: el("discover-ports").value.trim() || "45678",
          }),
        });
        window.__peerList = data.peers || [];
        renderPeerList(window.__peerList);
        setNotice("discover-notice", `发现完成，共 ${data.count || 0} 位龙虾。`);
      } catch (error) {
        setNotice("discover-notice", error.message, true);
      }
    }

    async function pair(peerId) {
      try {
        const data = await request("/clawsoc/api/pair", {
          method: "POST",
          body: JSON.stringify({ peerId }),
        });
        await loadState();
        await loadPeer(data.peer.peerId, true);
        setNotice("discover-notice", `已配对 ${data.peer.displayName || data.peer.peerId}`);
      } catch (error) {
        setNotice("discover-notice", error.message, true);
      }
    }

    async function sendMessage() {
      if (!state.selectedPeerId) {
        setNotice("chat-notice", "请先在左侧选中一位龙虾。", true);
        return;
      }
      const message = el("message-input").value.trim();
      if (!message) {
        setNotice("chat-notice", "先输入一句话再发送。", true);
        return;
      }
      try {
        await request("/clawsoc/api/message", {
          method: "POST",
          body: JSON.stringify({ peerId: state.selectedPeerId, message }),
        });
        el("message-input").value = "";
        await loadPeer(state.selectedPeerId);
        await loadState();
        setNotice("chat-notice", "消息已发送。");
      } catch (error) {
        setNotice("chat-notice", error.message, true);
      }
    }

    async function sendShare(shareType) {
      if (!state.selectedPeerId) {
        setNotice("chat-notice", "请先选中一位龙虾，再发快捷分享。", true);
        return;
      }
      try {
        await request("/clawsoc/api/share", {
          method: "POST",
          body: JSON.stringify({ peerId: state.selectedPeerId, shareType }),
        });
        await loadPeer(state.selectedPeerId);
        await loadState();
        setNotice("chat-notice", `已分享 ${shareType}`);
      } catch (error) {
        setNotice("chat-notice", error.message, true);
      }
    }

    async function requestUpgrade(level) {
      if (!state.selectedPeerId) {
        setNotice("chat-notice", "请先选中一位龙虾。", true);
        return;
      }
      try {
        await request("/clawsoc/api/relationship/upgrade", {
          method: "POST",
          body: JSON.stringify({ peerId: state.selectedPeerId, level }),
        });
        await loadPeer(state.selectedPeerId);
        await loadState();
        setNotice("chat-notice", `已发起升级请求：${level}`);
      } catch (error) {
        setNotice("chat-notice", error.message, true);
      }
    }

    async function acceptUpgrade() {
      if (!state.selectedPeerId) {
        setNotice("chat-notice", "请先选中一位龙虾。", true);
        return;
      }
      try {
        await request("/clawsoc/api/relationship/accept", {
          method: "POST",
          body: JSON.stringify({ peerId: state.selectedPeerId }),
        });
        await loadPeer(state.selectedPeerId);
        await loadState();
        setNotice("chat-notice", "已接受升级请求。");
      } catch (error) {
        setNotice("chat-notice", error.message, true);
      }
    }

    el("discover-btn").addEventListener("click", discover);
    el("refresh-peers").addEventListener("click", loadState);
    el("send-btn").addEventListener("click", sendMessage);
    el("message-input").addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        sendMessage();
      }
    });

    loadState().catch((error) => {
      setNotice("discover-notice", error.message, true);
      setNotice("chat-notice", error.message, true);
    });
    connectRealtime();
    setInterval(() => {
      if (state.eventSource && state.eventSource.readyState === EventSource.OPEN) {
        return;
      }
      loadState().catch(() => {});
      if (state.selectedPeerId) {
        loadPeer(state.selectedPeerId).catch(() => {});
      }
    }, 20000);
  </script>
</body>
</html>
"""


class ClawSocHandler(BaseHTTPRequestHandler):
    server_version = "ClawSoc/0.1"

    def _send_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _begin_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    def _write_sse(self, event: str, data: dict) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(payload)
        self.wfile.flush()

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

    def _load_state(self) -> dict:
        return load_state(self.app["paths"])

    def _health_payload(self, state: dict) -> dict:
        return {
            "ok": True,
            "peerId": state.get("identity", {}).get("id"),
            "displayName": state.get("identity", {}).get("displayName"),
            "emoji": state.get("identity", {}).get("emoji"),
            "bio": state.get("identity", {}).get("bio"),
            "endpoint": state.get("identity", {}).get("endpoint"),
            "port": state.get("settings", {}).get("port"),
        }

    def _normalize_peer(self, peer: dict) -> dict:
        return {
            **peer,
            "levelName": LEVEL_NAMES.get(peer.get("relationshipLevel", "L0"), peer.get("relationshipLevel", "L0")),
        }

    def _pending_upgrade_for_peer(self, state: dict, peer_id: str) -> tuple[dict | None, dict | None]:
        inbound = None
        outbound = None
        for item in state.get("pending", {}).get("upgradeRequests", []):
            if item.get("fromPeerId") == peer_id and item.get("status") == "pending-inbound":
                inbound = item
            if item.get("toPeerId") == peer_id and item.get("status") == "pending-outbound":
                outbound = item
        return inbound, outbound

    def _load_peer_history(self, peer_id: str, limit: int = 24) -> list[dict]:
        history_path = self.app["paths"].peers_dir / peer_id / "messages.jsonl"
        if not history_path.exists():
            return []
        records = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records[-limit:]

    def _stream_events(self) -> None:
        events_path = self.app["paths"].logs_dir / "events.jsonl"
        self._begin_sse()
        cursor = events_path.stat().st_size if events_path.exists() else 0
        self._write_sse("ready", {"ok": True})
        heartbeat_at = time.monotonic()
        while True:
            try:
                if events_path.exists():
                    size = events_path.stat().st_size
                    if size < cursor:
                        cursor = 0
                    if size > cursor:
                        with events_path.open("r", encoding="utf-8") as handle:
                            handle.seek(cursor)
                            for line in handle:
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    event = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                self._write_sse("update", event)
                            cursor = handle.tell()
                if time.monotonic() - heartbeat_at >= 12:
                    self._write_sse("ping", {"ok": True, "timestamp": utc_now()})
                    heartbeat_at = time.monotonic()
                time.sleep(0.8)
            except (BrokenPipeError, ConnectionResetError):
                return

    def _recent_share_summaries(self, peer_id: str, limit: int = 4) -> list[str]:
        share_dir = self.app["paths"].peers_dir / peer_id / "shares"
        if not share_dir.exists():
            return []
        summaries = []
        for path in sorted(share_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            share_type = data.get("shareType") or path.stem.split("-", 1)[-1]
            content = data.get("content", {})
            preview = ""
            if isinstance(content, dict):
                if "summary" in content:
                    preview = str(content["summary"])[:80]
                elif "skills" in content:
                    preview = f"{len(content['skills'])} 个技能"
                elif "tasks" in content:
                    preview = f"{len(content['tasks'])} 个任务"
                else:
                    preview = json.dumps(content, ensure_ascii=False)[:80]
            summaries.append(f"{share_type}: {preview or '已共享'}")
        return summaries

    def _quick_shares(self, level: str) -> list[str]:
        return [share_type for share_type, minimum in self.app["share_requirements"].items() if level_at_least(level, minimum)]

    def _discover_peers(self, state: dict, hosts: list[str], ports: list[int], timeout: float = 0.6) -> list[dict]:
        self_id = state.get("identity", {}).get("id")
        targets = [(host, port) for host in hosts for port in ports]

        def _probe(target: tuple[str, int]) -> dict | None:
            host, port = target
            url = f"http://{host}:{port}/clawsoc/health"
            request = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
                return None
            if not payload or not payload.get("ok") or payload.get("peerId") == self_id:
                return None
            peer_id = payload.get("peerId")
            if not peer_id:
                return None
            endpoint = payload.get("endpoint") or f"http://{host}:{port}"
            existing = state.get("peers", {}).get(peer_id, {})
            return {
                "peerId": peer_id,
                "displayName": payload.get("displayName") or peer_id,
                "nickname": payload.get("displayName") or peer_id,
                "emoji": payload.get("emoji") or "🐾",
                "bio": payload.get("bio") or "",
                "endpoint": endpoint,
                "relationshipLevel": existing.get("relationshipLevel", "L0"),
                "status": existing.get("status", "discovered"),
                "lastSeenAt": utc_now(),
            }

        discovered = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, min(64, len(targets) or 1))) as executor:
            for peer in executor.map(_probe, targets):
                if peer:
                    discovered.append(peer)
        deduped = {}
        for peer in discovered:
            deduped[peer["peerId"]] = peer
        return list(deduped.values())

    def _pair_with_peer(self, state: dict, peer_id: str | None = None, endpoint: str | None = None) -> dict:
        peer = None
        if peer_id:
            peer = state.get("peers", {}).get(peer_id)
            if not peer:
                raise SystemExit(f"Unknown peer: {peer_id}")
        endpoint = endpoint or (peer or {}).get("endpoint")
        if not endpoint:
            raise SystemExit("Missing peer endpoint")
        request = urllib.request.Request(f"{endpoint.rstrip('/')}/clawsoc/health", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                health = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise SystemExit(f"Peer unreachable: {exc.reason}") from exc
        remote_id = health.get("peerId")
        if not remote_id or remote_id == state.get("identity", {}).get("id"):
            raise SystemExit("Invalid remote peer")
        envelope = {
            "fromPeerId": state["identity"]["id"],
            "timestamp": utc_now(),
            "requestId": uuid.uuid4().hex,
            "type": "pair.request",
            "payload": {
                "displayName": state["identity"]["displayName"],
                "emoji": state["identity"].get("emoji"),
                "bio": state["identity"].get("bio"),
                "endpoint": state["identity"]["endpoint"],
            },
        }
        data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        pair_req = urllib.request.Request(
            f"{endpoint.rstrip('/')}/clawsoc/pair",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(pair_req, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SystemExit(exc.read().decode("utf-8", errors="ignore") or exc.reason) from exc
        identity = payload.get("identity", {})
        existing = state.get("peers", {}).get(remote_id, {})
        merged = upsert_peer(
            self.app["paths"],
            state,
            {
                "peerId": identity.get("peerId") or remote_id,
                "displayName": identity.get("displayName") or health.get("displayName") or remote_id,
                "nickname": identity.get("displayName") or health.get("displayName") or remote_id,
                "emoji": identity.get("emoji") or health.get("emoji"),
                "bio": identity.get("bio") or health.get("bio"),
                "endpoint": identity.get("endpoint") or endpoint.rstrip("/"),
                "relationshipLevel": existing.get("relationshipLevel", "L0"),
                "status": "active",
                "lastSeenAt": utc_now(),
            },
        )
        log_event(self.app["paths"], "pair.completed", {"peerId": merged["peerId"], "endpoint": endpoint, "requestId": envelope["requestId"]})
        save_state(self.app["paths"], state)
        return merged

    def _send_outbound_message(self, state: dict, peer_id: str, message: str) -> None:
        peer = state.get("peers", {}).get(peer_id)
        if not peer or peer.get("status") != "active":
            raise SystemExit(f"Peer {peer_id} not paired")
        envelope = {
            "fromPeerId": state["identity"]["id"],
            "timestamp": utc_now(),
            "requestId": uuid.uuid4().hex,
            "type": "chat.message",
            "payload": {"message": message},
        }
        data = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{peer['endpoint'].rstrip('/')}/clawsoc/message",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
            raise SystemExit(exc.read().decode("utf-8", errors="ignore") or exc.reason) from exc
        peer["lastMessageAt"] = utc_now()
        peer["lastSeenAt"] = utc_now()
        log_message(self.app["paths"], peer_id, "outbound", message, envelope["requestId"], {"type": "chat"})
        log_event(self.app["paths"], "message.sent", {"peerId": peer_id, "requestId": envelope["requestId"]})
        save_state(self.app["paths"], state)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        if route in {"/clawsoc/ui", "/clawsoc/ui/"}:
            self._send_html(200, WEB_UI_HTML)
            return
        if route == "/clawsoc/health":
            state = self._load_state()
            self._send(200, self._health_payload(state))
            return
        if route == "/clawsoc/api/state":
            state = self._load_state()
            peers = sorted(state.get("peers", {}).values(), key=lambda item: item.get("updatedAt") or "", reverse=True)
            self._send(
                200,
                {
                    "ok": True,
                    "identity": state.get("identity", {}),
                    "peers": [self._normalize_peer(peer) for peer in peers],
                },
            )
            return
        if route == "/clawsoc/api/events":
            self._stream_events()
            return
        if route == "/clawsoc/api/peer":
            peer_id = parse_qs(parsed.query).get("peerId", [""])[0]
            state = self._load_state()
            peer = state.get("peers", {}).get(peer_id)
            if not peer:
                self._send(404, {"ok": False, "error": f"Unknown peer: {peer_id}"})
                return
            normalized = self._normalize_peer(peer)
            normalized["history"] = self._load_peer_history(peer_id)
            normalized["recentShares"] = self._recent_share_summaries(peer_id)
            normalized["quickShares"] = self._quick_shares(peer.get("relationshipLevel", "L0"))
            inbound, outbound = self._pending_upgrade_for_peer(state, peer_id)
            normalized["pendingInboundUpgrade"] = inbound
            normalized["pendingOutboundUpgrade"] = outbound
            self._send(200, {"ok": True, "peer": normalized})
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
            elif route == "/clawsoc/api/discover":
                self._handle_ui_discover(payload)
            elif route == "/clawsoc/api/pair":
                self._handle_ui_pair(payload)
            elif route == "/clawsoc/api/message":
                self._handle_ui_message(payload)
            elif route == "/clawsoc/api/share":
                self._handle_ui_share(payload)
            elif route == "/clawsoc/api/relationship/upgrade":
                self._handle_ui_relationship_upgrade(payload)
            elif route == "/clawsoc/api/relationship/accept":
                self._handle_ui_relationship_accept(payload)
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

    def _handle_ui_discover(self, data: dict) -> None:
        state = self._load_state()
        hosts = [item.strip() for item in str(data.get("hosts", "")).split(",") if item.strip()]
        cidr = str(data.get("cidr", "")).strip()
        if cidr and not hosts:
            network = ipaddress.ip_network(cidr, strict=False)
            hosts = [str(host) for host in network.hosts()]
        if not hosts:
            endpoint = state.get("identity", {}).get("endpoint", "")
            host = "127.0.0.1"
            try:
                host = endpoint.split("://", 1)[1].rsplit(":", 1)[0]
            except IndexError:
                pass
            if host == "127.0.0.1":
                hosts = ["127.0.0.1"]
            else:
                try:
                    network = ipaddress.ip_network(f"{host}/24", strict=False)
                    hosts = [str(candidate) for candidate in network.hosts()]
                except ValueError:
                    hosts = [host]
        ports = sorted({int(port.strip()) for port in str(data.get("ports", "45678")).split(",") if port.strip()})
        peers = self._discover_peers(state, hosts, ports, timeout=0.6)
        for peer in peers:
            upsert_peer(self.app["paths"], state, peer)
        if peers:
            log_event(self.app["paths"], "discover.recorded", {"count": len(peers), "peerIds": [peer["peerId"] for peer in peers]})
            save_state(self.app["paths"], state)
        self._send(200, {"ok": True, "count": len(peers), "peers": [self._normalize_peer(peer) for peer in peers]})

    def _handle_ui_pair(self, data: dict) -> None:
        state = self._load_state()
        peer = self._pair_with_peer(state, peer_id=data.get("peerId"), endpoint=data.get("endpoint"))
        normalized = self._normalize_peer(peer)
        normalized["history"] = self._load_peer_history(peer["peerId"])
        normalized["recentShares"] = self._recent_share_summaries(peer["peerId"])
        normalized["quickShares"] = self._quick_shares(peer.get("relationshipLevel", "L0"))
        self._send(200, {"ok": True, "peer": normalized})

    def _handle_ui_message(self, data: dict) -> None:
        peer_id = str(data.get("peerId", "")).strip()
        message = str(data.get("message", "")).strip()
        if not peer_id or not message:
            raise SystemExit("peerId and message are required")
        state = self._load_state()
        self._send_outbound_message(state, peer_id, message)
        self._send(200, {"ok": True})

    def _handle_ui_share(self, data: dict) -> None:
        from sharing import build_share_content, sanitize_share_content

        state = self._load_state()
        peer_id = str(data.get("peerId", "")).strip()
        share_type = str(data.get("shareType", "")).strip()
        if not peer_id or not share_type:
            raise SystemExit("peerId and shareType are required")
        peer = state.get("peers", {}).get(peer_id)
        if not peer or peer.get("status") != "active":
            raise SystemExit(f"Peer {peer_id} not paired")
        minimum = self.app["share_requirements"].get(share_type)
        if not minimum:
            raise SystemExit(f"Unknown share type: {share_type}")
        if not level_at_least(peer.get("relationshipLevel", "L0"), minimum):
            raise SystemExit(f"Share type {share_type} requires {minimum}")
        content = build_share_content(
            share_type,
            workspace_root=self.app["paths"].workspace_root,
            identity=state["identity"],
            extra_keywords=state.get("settings", {}).get("redactionKeywords", []),
        )
        content = sanitize_share_content(content, state.get("settings", {}).get("redactionKeywords", []))
        envelope = {
            "fromPeerId": state["identity"]["id"],
            "timestamp": utc_now(),
            "requestId": uuid.uuid4().hex,
            "type": "share.send",
            "payload": {"item": ""},
            "shareType": share_type,
            "relationshipLevel": peer["relationshipLevel"],
            "redacted": True,
            "content": content,
        }
        request = urllib.request.Request(
            f"{peer['endpoint'].rstrip('/')}/clawsoc/share",
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
            raise SystemExit(exc.read().decode("utf-8", errors="ignore") or exc.reason) from exc
        peer["lastSharedAt"] = utc_now()
        peer["lastSeenAt"] = utc_now()
        log_share(self.app["paths"], peer_id, share_type, envelope, envelope["requestId"])
        log_event(self.app["paths"], "share.sent", {"peerId": peer_id, "shareType": share_type, "requestId": envelope["requestId"]})
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True})

    def _handle_ui_relationship_upgrade(self, data: dict) -> None:
        state = self._load_state()
        peer_id = str(data.get("peerId", "")).strip()
        target_level = normalize_level(str(data.get("level", "")).strip() or "L1")
        peer = state.get("peers", {}).get(peer_id)
        if not peer or peer.get("status") != "active":
            raise SystemExit(f"Peer {peer_id} not paired")
        envelope = {
            "fromPeerId": state["identity"]["id"],
            "timestamp": utc_now(),
            "requestId": uuid.uuid4().hex,
            "type": "relationship.upgrade",
            "payload": {"targetLevel": target_level},
        }
        request = urllib.request.Request(
            f"{peer['endpoint'].rstrip('/')}/clawsoc/relationship/upgrade",
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SystemExit(exc.read().decode("utf-8", errors="ignore") or exc.reason) from exc
        outbound = {
            "requestId": envelope["requestId"],
            "fromPeerId": state["identity"]["id"],
            "toPeerId": peer_id,
            "targetLevel": target_level,
            "createdAt": utc_now(),
            "status": "pending-outbound",
        }
        state["pending"]["upgradeRequests"] = [
            item for item in state["pending"]["upgradeRequests"] if item.get("toPeerId") != peer_id
        ]
        state["pending"]["upgradeRequests"].append(outbound)
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True, "request": payload.get("request", outbound)})

    def _handle_ui_relationship_accept(self, data: dict) -> None:
        state = self._load_state()
        peer_id = str(data.get("peerId", "")).strip()
        peer = state.get("peers", {}).get(peer_id)
        if not peer or peer.get("status") != "active":
            raise SystemExit(f"Peer {peer_id} not paired")
        request_item = None
        for item in state.get("pending", {}).get("upgradeRequests", []):
            if item.get("fromPeerId") == peer_id and item.get("status") == "pending-inbound":
                request_item = item
                break
        if not request_item:
            raise SystemExit(f"No pending upgrade request from {peer_id}")
        target_level = normalize_level(request_item["targetLevel"])
        peer["relationshipLevel"] = target_level
        peer["updatedAt"] = utc_now()
        envelope = {
            "fromPeerId": state["identity"]["id"],
            "timestamp": utc_now(),
            "requestId": uuid.uuid4().hex,
            "type": "relationship.accept",
            "payload": {
                "requestId": request_item["requestId"],
                "targetLevel": target_level,
            },
        }
        request = urllib.request.Request(
            f"{peer['endpoint'].rstrip('/')}/clawsoc/relationship/accept",
            data=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
            raise SystemExit(exc.read().decode("utf-8", errors="ignore") or exc.reason) from exc
        state["pending"]["upgradeRequests"] = [
            item for item in state["pending"]["upgradeRequests"] if item.get("requestId") != request_item["requestId"]
        ]
        save_state(self.app["paths"], state)
        self._send(200, {"ok": True, "peer": self._normalize_peer(peer)})


def serve(app: dict, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), ClawSocHandler)
    server.app = app  # type: ignore[attr-defined]
    print(f"ClawSoc server listening on http://{host}:{port}", flush=True)
    server.serve_forever()
