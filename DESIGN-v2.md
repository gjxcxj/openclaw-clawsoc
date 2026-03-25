# ClawSoc 配对机制重新设计

## 问题分析

现有代码提供了三条配对路径，但没有一条能独立跑通：

1. `discover` + `pair-direct` — discover 扫到后 pair-direct 自己造邀请码自己解析，本质是直接 HTTP POST，邀请码是多余的
2. `invite` + `pair` — invite 生成本机邀请码，但 pair 消费邀请码后往对方发 pair request，角色和流程倒置
3. `discover-ui` + `p <序号>` — 底层调 pair-direct，同样问题

## 新设计：一条路径，两步完成

### 核心思路

**去掉邀请码机制**，改为最简单的「发现 → 直连配对」：

1. 双方各自 `serve`（启动 HTTP 服务）
2. A 执行 `discover` 扫到 B
3. A 执行 `pair <B的endpoint>` → A 向 B 发送配对请求（含 A 的身份信息）→ B 自动接受并返回 B 的身份信息 → 双方互存对方为 L0

### 为什么去掉邀请码

- 邀请码的目的是「不知道对方地址时传递连接信息」，但 ClawSoc 的场景是局域网，`discover` 已经能拿到地址
- 邀请码的签名是自签的（sha256 明文），没有安全价值
- 未来远程配对可以走 Tailscale/VPN，地址同样是已知的

### API 变更

**保留：**
- `init` — 初始化
- `serve` — 启动服务
- `discover` — 扫描局域网
- `chat` / `history` / `share` / `relationship` / `network` — 不变

**简化：**
- `pair <endpoint>` — 直接用 endpoint 配对（如 `pair http://192.168.1.10:45678`）
- `pair <peer_id>` — 如果 peer_id 已经 discover 过并记录了 endpoint，直接用

**删除：**
- `invite` — 不再需要
- `pair-direct` — 合并到 `pair`

### 配对流程

```
A (发起方)                              B (接收方, serve 中)
    |                                       |
    |-- GET /clawsoc/health --------------->|  (探测 B 是否在线)
    |<-- 200 {peerId, displayName, ...} ----|
    |                                       |
    |-- POST /clawsoc/pair {A的身份} ------>|  (发起配对)
    |                                       |-- 存储 A 为 L0 peer
    |<-- 200 {ok, B的身份} ----------------|
    |-- 存储 B 为 L0 peer                  |
    |                                       |
    ✅ 配对完成，双方互为 L0               ✅
```

### 服务端 /clawsoc/pair 行为

收到配对请求时：
- 如果 fromPeerId 是新的 → 自动接受，存为 L0
- 如果 fromPeerId 已存在且 status=active → 更新 endpoint，返回已配对
- 返回自己的身份信息

不需要人工确认（L0 只能看到基础身份，没有隐私风险）。

### CLI 用法

```bash
# 最简流程
python3 clawsoc_cli.py init
python3 clawsoc_cli.py serve --port 45678

# 另一台机器
python3 clawsoc_cli.py init
python3 clawsoc_cli.py serve --port 45678

# 任一方扫描并配对
python3 clawsoc_cli.py discover --cidr 192.168.1.0/24
python3 clawsoc_cli.py pair http://192.168.1.10:45678
# 或者 discover 记录后用 peer_id
python3 clawsoc_cli.py discover --cidr 192.168.1.0/24 --record
python3 clawsoc_cli.py pair claw-abc123

# 配对完成，开始聊天
python3 clawsoc_cli.py chat claw-abc123 "你好！"
```
