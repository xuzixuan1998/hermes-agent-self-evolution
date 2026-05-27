# EDPAgent 部署指南

四种部署方式并列，选一种即可。公共前置依赖见 §0。

---

## 目录

- [0. 公共前置依赖](#0-公共前置依赖)
- [A. Linux 独立安装](#a-linux-独立安装)
- [B. Windows 独立安装](#b-windows-独立安装)
- [C. Docker 离线打包部署](#c-docker-离线打包部署)
- [D. Windows Docker 打包（连网 Windows 构建机）](#d-windows-docker-打包连网-windows-构建机)
- [E. 配置字段参考](#e-配置字段参考)
- [F. 常见问题](#f-常见问题)

---

## 0. 公共前置依赖

无论走哪种方式，目标机都需要下列服务可访问：

| 依赖 | 版本/说明 | 为什么 |
|---|---|---|
| **Redis** | **≥ 6.0**（强制） | `redis-py ≥ 4.5` 使用 `HELLO` 命令建连，Redis 5.x 及以下返回 `unknown command 'HELLO'` 会直接启动失败 |
| **LLM 网关** | OpenAI 兼容（`/v1/chat/completions`） | 例：`glm-5` / `deepseek-chat` / 企业自建 GLM / 银行 AI 中台 |
| **Versatile 低码平台 URL** | 含 `{project_id}` / `{agent_id}` / `{conversation_id}` 占位符 | VersatileAdapter 代理的上游 |
| **Python** | 3.11.x | 离线打包方式下由镜像内置，独立安装需自备 |

### 源码仓库

| 仓库 | 克隆地址 | 分支 |
|---|---|---|
| agent-runtime | `https://gitcode.com/openJiuwen/agent-runtime.git` | `feature/procode_enhancement` |
| agent-store | `https://gitcode.com/openJiuwen/agent-store.git` | `EDP-agent` |

### Skill 业务包（不在开源仓内）

EDPAgent 的 skill（含银行产品 ID、固定参数、卡片话术、沙箱脚本等敏感业务逻辑）**不发布
到开源社区**，由**现场部署同事**通过内部渠道提供。

请联系现场同事获取与目标部署对应的完整 skill 内容，**在 §A.3 / §C.1.3 之前**放入
`agent-store/community/EDPAgent/skills/`，后续 rsync / `build.sh` 会自动合入运行包。

**没有 skill 时 agent 也能起来**——`/health` 双 200 + 简单问候 SSE 都通——但**任何业务
请求**（理财推荐、资金筹划、产品选品等）会因为没有可调用的 skill 而无效。

---

## A. Linux 独立安装

**适用：**本机能联网装 Python 包，想直接从源码跑起来调试或单机部署。

### A.1 安装 Python + uv

```bash
# 1) uv（推荐的快速包管理器，自带 Python 多版本管理）
sudo apt-get update && sudo apt-get install -y git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.cargo/env   # 或 ~/.local/bin 加到 PATH
uv --version

# 2) Python 3.11（用 uv 管理，避免依赖系统 apt 的 deadsnakes PPA）
uv python install 3.11
```

> **为什么不用 apt 装 Python**：Ubuntu 22.04 默认仓库是 3.10、24.04 默认是 3.12，3.11 需要加 deadsnakes PPA。
> 而 openjiuwen 0.1.11 在 3.12 上的兼容性未经验证。`uv python install` 直接拉一个独立的 3.11，不污染系统环境。

### A.2 拉取两个仓

```bash
mkdir -p ~/EDPAgent && cd ~/EDPAgent

git clone https://gitcode.com/openJiuwen/agent-runtime.git
cd agent-runtime
git checkout feature/procode_enhancement
cd ..

git clone https://gitcode.com/openJiuwen/agent-store.git
cd agent-store
git checkout EDP-agent
cd ..
```

### A.3 合并 EDPAgent 业务代码到框架

```bash
cd ~/EDPAgent
# 目标目录：agent-runtime/applications/a2a_service/agents/EDPAgent/
TARGET=agent-runtime/applications/a2a_service/agents/EDPAgent
rm -rf "$TARGET"
mkdir -p "$TARGET"

# ⚠️ 两个路径末尾的斜杠都不能省（rsync 行为差异巨大）：
#    有斜杠 → 把 EDPAgent 内容展开到 TARGET/
#    没斜杠 → 把 EDPAgent 整个目录塞进 TARGET/EDPAgent/（导入会失败）
rsync -a --exclude='__pycache__/' --exclude='*.pyc' \
      --exclude='docs/' --exclude='deployment/' \
      agent-store/community/EDPAgent/ "$TARGET/"

# 验证目录结构 —— 必须直接看到 agent.py 等文件，而不是 EDPAgent 子目录
ls "$TARGET"
# 正确：AgentRule.md  __init__.py  adapter.py  agent.py  agent_rule.py  config.py  rail/  ...
# 错误：EDPAgent/          ← 多了一层，说明 rsync 末尾斜杠漏了，重做

# 再做一次机器校验（文件必须存在）
test -f "$TARGET/agent.py" && test -f "$TARGET/__init__.py" \
  && echo "✅ 结构正确" || echo "❌ 目录结构错了，请检查 rsync 命令"
```

### A.4 建虚拟环境与装依赖

```bash
cd ~/EDPAgent/agent-runtime
uv venv -p 3.11 .venv     # 必须显式 -p 3.11；省略会用系统 Python（24.04 是 3.12，不兼容）
source .venv/bin/activate
python --version          # 期望 Python 3.11.x

# openjiuwen 0.1.11 依赖 a2a-sdk==1.0.0a0，本项目需 1.0.0a1，两步装
uv pip install --prerelease=allow \
    'openjiuwen==0.1.11' \
    'pydantic>=2.7.0' 'pydantic-settings>=2.3.0' \
    'loguru>=0.7.0' 'aiosqlite>=0.19.0' 'sse-starlette>=3.3.0' \
    'fastapi>=0.111.0' 'uvicorn[standard]>=0.30.0' \
    'httpx>=0.27.0' 'redis>=5.0.0' 'protobuf>=4.25.0' \
    'python-dotenv>=1.0' 'pyyaml>=6.0'

uv pip install --prerelease=allow \
    --reinstall-package a2a-sdk 'a2a-sdk==1.0.0a1'
```

### A.5 预置 tiktoken 编码文件（规避 openjiuwen 0.1.11 已知 bug）

```bash
# 必须放在这个目录，文件名必须是 SHA1 哈希
mkdir -p ~/.tiktoken_cache
curl -fsSL -o ~/.tiktoken_cache/9b5ad71b2ce5302211f9c61530b329a4922fc6a4 \
    https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken

# 启动前导出环境变量
export TIKTOKEN_CACHE_DIR=~/.tiktoken_cache
```

### A.6 配置 .env

```bash
cd ~/EDPAgent/agent-runtime/applications

# a2a_service 配置
cp a2a_service/.env.example a2a_service/.env
vim a2a_service/.env       # 见 §E 字段表

# versatile_adapter 配置
cp versatile_adapter/.env.example versatile_adapter/.env
vim versatile_adapter/.env   # 见 §E 字段表
```

> **关键约束 ①**：`SKILL_LLM_TLS_VERIFY` **必须保持 `false`**（除非你同时配齐 `ssl_cert` 路径）。
> openjiuwen 0.1.11 的 `BaseModelClient` 校验：`verify_ssl=True` 但 `ssl_cert=None` 时直接报
> `[181002] model client config ssl_cert is required when verify_ssl is True` 拒绝创建客户端。
>
> **关键约束 ②**：本机原生跑（§A 路径）时 `REDIS_HOST=127.0.0.1` 或具体 IP；走 §C Docker 时
> 用 `host.docker.internal`（`run.sh` 已带 `--add-host host.docker.internal:host-gateway`）。
> 两条路径的 .env **不能直接通用**。

### A.7 启动（两个终端分别跑）

**终端 1：先启动 versatile_adapter**
```bash
cd ~/EDPAgent/agent-runtime/applications/versatile_adapter
source ../../.venv/bin/activate
export TIKTOKEN_CACHE_DIR=~/.tiktoken_cache
python main.py
# 等看到 "Uvicorn running on http://0.0.0.0:8091"
```

**终端 2：再启动 a2a_service**
```bash
cd ~/EDPAgent/agent-runtime/applications/a2a_service
source ../../.venv/bin/activate
export TIKTOKEN_CACHE_DIR=~/.tiktoken_cache
python main.py
# 等看到 "Uvicorn running on http://0.0.0.0:8090"
```

### A.8 验证

**冒烟（健康检查）：**
```bash
curl http://localhost:8090/health   # {"status":"healthy","service":"A2A Service"}
curl http://localhost:8091/health   # {"status":"healthy","service":"VersatileAdapter"}
```

**端到端骨架（仅冒烟用，不验证业务正确性）：**
```bash
curl -sN -X POST -H 'Content-Type: application/json' \
  "http://localhost:8090/v1/edp/agents/edp_agent/conversations/smoke-$(date +%s)" \
  -d '{"input":{"query":"你好"}}'
```

期望看到一段 SSE 流，至少包含 `conversation_start` → `final_answer_start` → `final_answer_chunk` →
`final_answer_end` → `conversation_end` → `[DONE]`。任意 `final_answer_chunk.content` 非空即代表
LLM 通了，agent 跑出真实输出。

> **请求体 schema（容易踩坑）**：必须是 `{"input": {"query": "..."}}`，**不是** OpenAI 风格的
> `{"input": {"messages":[...]}}`。后者会被路由识别为空 query，agent 立刻结束、只输出
> `conversation_start` + `conversation_end` 两个事件，看着像"通了但什么都没说"。

业务端到端的真实业务正确性（含具体话术、Versatile 工作流响应、网关鉴权等）请**咨询现场同事**——
真实环境下网关路径、自定义 header、Versatile URL 与本机直连不同，本指南只覆盖骨架链路。

### A.9 可选：systemd 开机自启

参考 `deployment/` 下的脚本改造为 systemd unit，或使用 supervisord / PM2 托管两个进程。

---

## B. Windows 独立安装

**适用：**客户机为 Windows Server / 开发机，没有 WSL，希望直接原生运行。

> **强烈建议：**如 Windows 机能启用 WSL2，优先走 **§A Linux 独立安装**（在 WSL 内），体验更稳定。原生 Windows 有以下限制：
> - Redis 官方不提供 Windows 版（需用 Memurai 等兼容实现或远程 Redis）
> - 进程管理只能靠 PowerShell 手动启动或 NSSM

### B.1 装 Python 3.11

下载 <https://www.python.org/downloads/windows/>，安装时勾选 "Add python.exe to PATH"。

PowerShell 验证：
```powershell
python --version       # Python 3.11.x
pip --version
```

### B.2 装 uv（可选，也可以用 pip）

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
# 重开 PowerShell 后验证
uv --version
```

### B.3 Redis 准备

三选一：
- **远程 Redis**：用企业已有的 Redis 6+ 集群，最省事
- **Memurai**：<https://www.memurai.com/>（Redis 6 API 兼容的 Windows 原生产品，社区版免费）
- **Docker Desktop 起一个 Redis 容器**：`docker run -d --name redis -p 6379:6379 redis:7-alpine`

### B.4 拉取两个仓

```powershell
mkdir $HOME\EDPAgent
cd $HOME\EDPAgent

git clone https://gitcode.com/openJiuwen/agent-runtime.git
cd agent-runtime
git checkout feature/procode_enhancement
cd ..

git clone https://gitcode.com/openJiuwen/agent-store.git
cd agent-store
git checkout EDP-agent
cd ..
```

### B.5 合并 EDPAgent 代码

```powershell
$target = "$HOME\EDPAgent\agent-runtime\applications\a2a_service\agents\EDPAgent"
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Path $target | Out-Null

# robocopy 是"拷贝 SOURCE 的内容到 DEST"，不会多出一层 EDPAgent 目录
robocopy `
    "$HOME\EDPAgent\agent-store\community\EDPAgent" `
    $target `
    /E /XD docs deployment __pycache__ /XF *.pyc

# 验证目录结构 —— 必须直接看到 agent.py 等文件
Get-ChildItem $target
# 正确输出应包含：AgentRule.md / agent.py / __init__.py / rail / ...
# 错误输出包含 EDPAgent 子目录 → 说明 robocopy 命令写错，重做

if ((Test-Path "$target\agent.py") -and (Test-Path "$target\__init__.py")) {
    Write-Host "✅ 结构正确"
} else {
    Write-Host "❌ 目录结构错了，请检查 robocopy 命令"
}
```

### B.6 建虚拟环境与装依赖

```powershell
cd $HOME\EDPAgent\agent-runtime
uv venv -p 3.11 .venv   # 显式 -p 3.11；或 python -m venv .venv（系统 Python 须为 3.11.x）
.\.venv\Scripts\Activate.ps1

uv pip install --prerelease=allow `
    "openjiuwen==0.1.11" `
    "pydantic>=2.7.0" "pydantic-settings>=2.3.0" `
    "loguru>=0.7.0" "aiosqlite>=0.19.0" "sse-starlette>=3.3.0" `
    "fastapi>=0.111.0" "uvicorn[standard]>=0.30.0" `
    "httpx>=0.27.0" "redis>=5.0.0" "protobuf>=4.25.0" `
    "python-dotenv>=1.0" "pyyaml>=6.0"

uv pip install --prerelease=allow `
    --reinstall-package a2a-sdk "a2a-sdk==1.0.0a1"
```

### B.7 预置 tiktoken 编码文件

```powershell
$cache = "$HOME\.tiktoken_cache"
New-Item -ItemType Directory -Path $cache -Force | Out-Null
Invoke-WebRequest `
    -Uri "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken" `
    -OutFile "$cache\9b5ad71b2ce5302211f9c61530b329a4922fc6a4"

$env:TIKTOKEN_CACHE_DIR = $cache
```

### B.8 配置 .env

```powershell
cd $HOME\EDPAgent\agent-runtime\applications

Copy-Item a2a_service\.env.example  a2a_service\.env
Copy-Item versatile_adapter\.env.example  versatile_adapter\.env

notepad a2a_service\.env
notepad versatile_adapter\.env
```

> **Windows 编辑注意**：保存为 **UTF-8** + **LF** 换行（非 CRLF），否则在某些场景会引发解析异常。VS Code 右下角可切换。

### B.9 启动（两个 PowerShell 终端）

**PS 1：**
```powershell
cd $HOME\EDPAgent\agent-runtime\applications\versatile_adapter
..\..\.venv\Scripts\Activate.ps1
$env:TIKTOKEN_CACHE_DIR = "$HOME\.tiktoken_cache"
python main.py
```

**PS 2：**
```powershell
cd $HOME\EDPAgent\agent-runtime\applications\a2a_service
..\..\.venv\Scripts\Activate.ps1
$env:TIKTOKEN_CACHE_DIR = "$HOME\.tiktoken_cache"
python main.py
```

### B.10 验证

```powershell
curl http://localhost:8090/health
curl http://localhost:8091/health
```

业务端到端功能测试请**咨询现场同事**。

### B.11 可选：NSSM 注册 Windows 服务

使用 [NSSM](https://nssm.cc/) 将两个 `python main.py` 注册为 Windows 服务实现开机自启。

---

## C. Docker 离线打包部署

**适用：**客户机离线 / 不能联网装 Python 包 / 需一键启停。一台**连网构建机**产出 tar.gz，拷到**离线部署机** `docker load` 即可。

```
┌──────────────────────┐                  ┌──────────────────────┐
│ 连网构建机            │                  │ 离线部署机            │
│                      │                  │                      │
│ 1) clone 两仓        │    scp/tar.gz    │ 1) tar -xzf          │
│ 2) ./build.sh        │─────────────────▶│ 2) ./import-bundle   │
│ 3) ./export-bundle   │                  │ 3) 填 .env           │
│                      │                  │ 4) ./run.sh          │
└──────────────────────┘                  └──────────────────────┘
```

离线部署机要求：**仅 Docker ≥ 20.10**（含 `--init` 支持），不需要 Python、docker compose、git。

### C.1 构建端（连网机）

#### C.1.1 准备

```bash
# 需要：Docker、git、bash
docker version     # >= 20.10
```

#### C.1.2 拉仓

```bash
mkdir -p ~/EDPAgent && cd ~/EDPAgent

git clone https://gitcode.com/openJiuwen/agent-runtime.git
cd agent-runtime
git checkout feature/procode_enhancement
cd ..

git clone https://gitcode.com/openJiuwen/agent-store.git
cd agent-store
git checkout EDP-agent
cd ..
```

#### C.1.3 构建镜像

```bash
cd ~/EDPAgent/agent-store/community/EDPAgent/deployment

# 新仓 fresh clone 后 .sh 文件没有执行位（Linux 端 git 默认未提交 +x），先补
chmod +x *.sh

./build.sh
```

`build.sh` 自动完成：
1. 从 `agent-runtime/applications/` 拷贝 `a2a_service` + `versatile_adapter`
2. 合并 `agent-store/community/EDPAgent/` 到 `a2a_service/agents/EDPAgent/`（rsync 自动排除 `docs/` `deployment/` `__pycache__/` `*.pyc`）
3. `docker build -t edpagent:latest`。镜像内置：
   - Python 3.11 + 全部 Python 依赖
   - tiktoken `cl100k_base` 编码文件（规避 openjiuwen 0.1.11 运行时下载 bug）
   - 双进程 entrypoint 启动脚本
   - 运维排障工具：`vim` / `less` / `procps` (ps/top) / `iproute2` (ss/ip) / `net-tools` (netstat) / `iputils-ping` / `dnsutils` (dig)

首次构建（本地无 apt/pip 缓存）耗时 2~5 分钟，有缓存时 < 30 秒；产物镜像约 **1.24 GB**。

#### C.1.4 导出离线包

```bash
./export-bundle.sh
```

产物：`bundle/edpagent-offline-<日期>.tar.gz`（约 **263 MB**）

包含：

| 文件 | 作用 |
|---|---|
| `edpagent.image.tar` | Docker 镜像（`docker save` 产出） |
| `import-bundle.sh` | 客户侧 `docker load` 脚本 |
| `run.sh` / `stop.sh` | 启停脚本 |
| `config/a2a_service.env.example` | a2a 服务配置模板 |
| `config/versatile_adapter.env.example` | VA 配置模板 |
| `README.md` | 客户侧快速入门 |

### C.2 部署端（离线机）

#### C.2.1 前置检查

```bash
docker version                               # ≥ 20.10
redis-cli -h <Redis-IP> -p 6379 ping         # PONG
redis-cli -h <Redis-IP> -p 6379 INFO server | grep redis_version   # ≥ 6.0
```

#### C.2.2 导入镜像

```bash
cd /opt   # 或任何部署目录
tar xzf edpagent-offline-<日期>.tar.gz
cd edpagent-offline-<日期>

./import-bundle.sh
```

#### C.2.3 填写配置

```bash
cp config/a2a_service.env.example       config/a2a_service.env
cp config/versatile_adapter.env.example config/versatile_adapter.env

vim config/a2a_service.env          # 见 §E
vim config/versatile_adapter.env    # 见 §E
```

> **关键约束：**模板已预先加引号/加前缀，保持原字段名直接**改值**即可，不要乱改名。

#### C.2.4 启动

```bash
./run.sh
```

等同：
```bash
docker run -d --name edpagent \
  --init \
  --add-host host.docker.internal:host-gateway \
  -p 8090:8090 -p 8091:8091 \
  -v "$PWD/config:/etc/edpagent/config:ro" \
  -v "$PWD/logs:/var/log/edpagent" \
  --restart unless-stopped \
  edpagent:latest
```

启动脚本会轮询健康检查最多 60 秒（容器内 entrypoint 对 VersatileAdapter 也留 60 秒）。就绪后打印容器状态。

#### C.2.5 验证

```bash
curl http://localhost:8090/health
curl http://localhost:8091/health
docker top edpagent
docker logs -f edpagent
```

业务端到端功能测试（往 agent 端点 POST 消息、收 SSE 流）请**咨询现场同事** —— 真实环境下网关路径与鉴权方式与本机直连不同，本指南暂不给出具体命令。

#### C.2.6 运维命令

```bash
./stop.sh                            # 停止并删除容器
docker restart edpagent              # 保持配置重启
docker logs --tail 200 edpagent      # 最近 200 行日志
docker inspect edpagent --format '{{.State.Status}}'
```

### C.3 升级与协作

> **关键分工**：`build.sh` 做两次独立的 rsync ——
> - `agent-runtime/applications/` → 框架层（a2a_service + versatile_adapter）
> - `agent-store/community/EDPAgent/` → 业务层（EDPAgent 本体）
>
> 因此**业务改动与框架改动可以独立发版**。

#### C.3.1 只改 store 侧（最常见场景）

同事在 `agent-store/community/EDPAgent/` 下改了 `AgentRule.md` / `agent.py` / `tool/*.py` / `rail/*.py` / `prompt.py` 等业务文件，**不动 agent-runtime**。

**构建端 SOP：**
```bash
# 1) 同步两个仓（即使 runtime 没改，也校验干净状态）
cd ~/EDPAgent/agent-store
git fetch origin && git checkout EDP-agent && git pull origin EDP-agent

cd ~/EDPAgent/agent-runtime
git status   # 应干净；若 HEAD 变化请同步分支

# 2) 重新打包
cd ~/EDPAgent/agent-store/community/EDPAgent/deployment
./build.sh              # rsync 自动带上所有 store 侧改动
./export-bundle.sh
```

**store 侧改动中**自动生效的：`AgentRule.md`（YAML + prompt）、`tool/*.py`、`rail/*.py`、`adapter.py`、`agent.py`、`config.py`、`prompt.py`、`state_keys.py`、`skills/*`、`__init__.py`。

**需要手工配套**的三类坑：
| 改动类型 | 为什么 build.sh 不够 | 怎么办 |
|---|---|---|
| 新增 Python 依赖（改 `pyproject.toml`） | Dockerfile 的 `pip install` 列表是硬编码的，不读 pyproject | 同步把新包加到 `deployment/Dockerfile` 的 uv pip install 行 |
| 新增工具并期望走 Versatile | `rail/versatile_interrupt_rail.py` 的拦截清单要带上新工具名，Versatile 侧也要有对应工作流 | 跨系统变更：改拦截清单 + 找低码平台同事配工作流 |
| 改 `AgentRule.md` 的 **YAML frontmatter** 字段名 | `agent_rule.py` 按字段名解析，改名会导致配置失效 | 只改 YAML `value`，或同步改 `agent_rule.py` 的 schema |

**部署端**：
```bash
./stop.sh
./import-bundle.sh      # docker load 覆盖 latest tag
./run.sh                # 原 config/ 保留不动
```

#### C.3.2 框架侧（agent-runtime）也有变更

走完整流程：两个仓都 `git pull`，然后 build + export 同上。注意 agent-runtime 的分支基线在文档顶部 §"源码仓库" 表里。

---

## D. Windows Docker 打包（连网 Windows 构建机）

**适用：**连网构建机是 Windows 且**没有 WSL**，只有 PowerShell 或 CMD，但装了 Docker Desktop。此时 §C.1 的 `build.sh` / `export-bundle.sh` 跑不了（依赖 `rsync` 和 bash），走这一章。

产物与 §C.1 的 `bundle\edpagent-offline-*.tar.gz` 完全一致（bit-compatible），客户侧流程沿用 §C.2。

```
┌──────────────────────┐                  ┌──────────────────────┐
│ Windows 连网构建机    │                  │ 离线 Linux 服务器     │
│                      │                  │                      │
│ 1) git clone 两仓    │    scp/WinSCP    │ 1) tar -xzf          │
│ 2) .\build-and-     │─────────────────▶│ 2) chmod +x *.sh ⚠   │
│    export.ps1        │                  │ 3) ./import-bundle   │
│                      │                  │ 4) 填 .env           │
│                      │                  │ 5) ./run.sh          │
└──────────────────────┘                  └──────────────────────┘
```

### D.1 前置条件

| 组件 | 检查命令（PowerShell） | 说明 |
|---|---|---|
| Docker Desktop（**Linux containers 模式**） | `docker version`（Server 段 OS 是 `linux`） | 右下角托盘图标 → Switch to Linux containers… |
| Git for Windows | `git --version` | <https://git-scm.com/download/win> |
| Windows 10 **1803+** 自带 tar.exe | `tar --version`（bsdtar） | 更老版本需装 7-Zip 手动改打包步骤 |
| PowerShell 执行策略允许脚本 | 见 §D.3 | 默认 Restricted 会拦 `.ps1` |

**防坑（最重要的一步）：** Git 默认 `core.autocrlf=true` 会把 `.sh` 文件的 LF 转成 CRLF，镜像构建进去后，容器启动时报 `env: 'bash\r': No such file or directory`。**clone 前先关掉**：

```powershell
git config --global core.autocrlf false
```

如果已经用默认设置 clone 过仓，**删掉目录重 clone**（脚本 Preflight 会检测 CRLF 并弹 y/N 确认提示兜底，但最干净的做法是 clone 前就关掉）。

### D.2 拉两个仓

```powershell
$Root = "$HOME\EDPAgent"
New-Item -ItemType Directory -Force -Path $Root | Out-Null
Set-Location $Root

git clone https://gitcode.com/openJiuwen/agent-runtime.git
Set-Location agent-runtime
git checkout feature/procode_enhancement
Set-Location ..

git clone https://gitcode.com/openJiuwen/agent-store.git
Set-Location agent-store
git checkout EDP-agent
Set-Location ..
```

### D.3 一键构建 + 打包

```powershell
cd $HOME\EDPAgent\agent-store\community\EDPAgent\deployment
.\build-and-export.ps1
```

**首次跑被执行策略拦截**时，一次性放开当前用户：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**非默认路径**（源码放在 `D:\repos\...` 等位置）：

```powershell
.\build-and-export.ps1 -AgentRuntime D:\repos\agent-runtime -AgentStore D:\repos\agent-store
```

**其他开关**（迭代发版常用）：

```powershell
.\build-and-export.ps1 -SkipBuild     # 只重新打包已有镜像，不重跑 docker build
.\build-and-export.ps1 -SkipExport    # 只 build，不出离线包
```

脚本内部做了什么（与 `build.sh` + `export-bundle.sh` 一对一等价）：

| bash 版动作 | PowerShell 版等价 | 备注 |
|---|---|---|
| `rsync -a --exclude=...` ×3 | `robocopy /E /XD ... /XF ...` ×3 | 排除规则对齐（`__pycache__` / `logs` / `.venv` / `*.pyc` / EDPAgent 侧额外 `docs` / `deployment`） |
| `docker build` | 同左 | |
| `docker save` | 同左 | |
| `tar czf` | `tar -czf` | 用 Windows 自带 bsdtar |
| Preflight 校验 docker / tar / 输入目录 | 同左 | 额外扫 `entrypoint.sh` 的 CRLF，命中弹 y/N |
| `chmod +x` | **无** | Windows 文件系统没有 +x 位，离线 Linux 侧必须补（见 §D.5） |

产物：`$HOME\EDPAgent\agent-store\community\EDPAgent\deployment\bundle\edpagent-offline-<时间戳>.tar.gz`，约 **263 MB**。

### D.4 传输到离线 Linux 服务器

**Windows 10+ 自带 scp**（推荐，PowerShell 直接能跑）：

```powershell
$Bundle = "$HOME\EDPAgent\agent-store\community\EDPAgent\deployment\bundle\edpagent-offline-<时间戳>.tar.gz"
scp $Bundle user@<离线服务器 IP>:/opt/
```

**WinSCP / U 盘 / 跳板机**：按企业通道传，没有 SSH 直连时用图形工具最省事。

### D.5 离线 Linux 服务器侧部署

**跟 §C.2 大部分一致**，只多两步一次性修复：

```bash
cd /opt
tar xzf edpagent-offline-<时间戳>.tar.gz
cd edpagent-offline-<时间戳>

# ⚠ Windows 打包必做：补执行位、修 CRLF（Windows 侧编辑过 .example 时可能混进 \r）
chmod +x import-bundle.sh run.sh stop.sh
sed -i 's/\r$//' config/*.example 2>/dev/null || true

./import-bundle.sh
cp config/a2a_service.env.example       config/a2a_service.env
cp config/versatile_adapter.env.example config/versatile_adapter.env
vim config/a2a_service.env              # 字段详见 §E.1
vim config/versatile_adapter.env        # 字段详见 §E.2

./run.sh
curl http://localhost:8090/health
curl http://localhost:8091/health
```

业务端到端测试请**咨询现场同事**（网关路径/鉴权差异同 §C.2.5）。

### D.6 迭代发版

同 §C.3 的分工一致：改 store 侧（业务代码）还是框架侧（agent-runtime），用法和坑表完全相通。PowerShell 侧每次新的打包就是：

```powershell
cd $HOME\EDPAgent\agent-store
git pull origin EDP-agent       # 或 myfork <你的分支>
cd $HOME\EDPAgent\agent-runtime
git pull origin feature/procode_enhancement   # 只在框架变更时需要
cd $HOME\EDPAgent\agent-store\community\EDPAgent\deployment
.\build-and-export.ps1
```

### D.7 Windows 专属坑表（§F 常见问题的 Windows 补充）

| 现象 | 原因 | 处理 |
|---|---|---|
| 容器启动报 `env: 'bash\r': No such file or directory` | Git 默认 `core.autocrlf=true` 把 `.sh` 改成 CRLF 烘进镜像 | §D.1 关 autocrlf，删仓重 clone，重跑脚本 |
| `docker version` 只有 Client，没 Server | Docker Desktop 未启动 / 未切 Linux containers | 启动 Docker Desktop，托盘右键 Switch to Linux containers… |
| `robocopy` 命令行看到 `exit 1/3/7` | robocopy 退出码 0-7 都算成功（`1=文件已复制`） | 脚本里 `Invoke-Robocopy` 已做归一化，可忽略日志里的数字 |
| `docker save` 慢/产物超大 | 工作目录在 `/mnt/c` 或 OneDrive 同步盘 | 挪到本地盘根，关掉该目录的 OneDrive 同步 |
| `docker build` 中途被 OOM killed | Docker Desktop 默认 RAM/Disk 上限偏小 | Settings → Resources 调大 CPU/RAM/Disk |
| 传输后 Linux 侧 `./run.sh` 报 permission denied | Windows 文件系统不保留 +x | §D.5 已包含 `chmod +x` 步骤 |
| Linux 侧 `.env` 启动异常但字段看着对 | `.env` 在 Windows 编辑时带了 CRLF | §D.5 的 `sed -i 's/\r$//'` 已处理 |
| PowerShell 提示 "无法加载脚本因为运行脚本被禁用" | 默认 Restricted 执行策略 | §D.3 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `tar: command not found` 或类似 | Windows < 1803 没有自带 tar.exe | 装 Git for Windows（含 tar）或换 7-Zip 手动打包 |

---

## E. 配置字段参考

### E.1 `a2a_service.env` —— a2a 服务主配置

```env
# ── 服务基本信息（含空格值必须加引号！）────────────────
APP_NAME="A2A Service"

# ── Redis（会话状态 / Checkpoint）──────────────────────
REDIS_HOST=<Redis IP 或 DNS>
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=<如有>
REDIS_SESSION_TTL=1800
REDIS_CHECKPOINTER_TTL_MINUTES=60

# ── 启动协调（多实例 Redis 锁；单实例保持 False）────────
BOOTSTRAP_COORDINATION_ENABLED=False
BOOTSTRAP_LOCK_NAME=a2a_global_bootstrap
BOOTSTRAP_LOCK_TTL_SEC=180
BOOTSTRAP_WAIT_TIMEOUT_SEC=300
BOOTSTRAP_POLL_INTERVAL_SEC=1.0

# ── 入口限流（每 IP / 全局）─────────────────────────
RATE_LIMIT_MAX_REQUESTS=5
RATE_LIMIT_WINDOW_SECONDS=120
GLOBAL_RATE_LIMIT_MAX_REQUESTS=100
GLOBAL_RATE_LIMIT_WINDOW_SECONDS=30

# ── VersatileAdapter 内部地址 ────────────────────────
# 同机部署：localhost  /  Docker 同容器：localhost
VERSATILE_ADAPTER_URL=http://localhost:8091

# ── FastAPI ────────────────────────────────────────
FASTAPI_HOST=0.0.0.0
FASTAPI_PORT=8090
FASTAPI_DEBUG=False
FASTAPI_WORKERS=1

# ── 日志 ───────────────────────────────────────────
LOG_LEVEL=INFO
LOG_FILE=logs/a2a_service.log

# ── LLM 网关 ────────────────────────────────────────
PLANNING_AGENT_MODEL_NAME=glm-5
PLANNING_AGENT_MODEL_API_KEY=<API Key>
PLANNING_AGENT_MODEL_BASE_URL=https://gw.example.com/v1
PLANNING_AGENT_MODEL_TIMEOUT=120
SKILL_LLM_TLS_VERIFY=false   # 必须 false（除非配套提供 ssl_cert 路径，见 §A.6 关键约束 ①）

# ── LLM 自定义请求头（成对配置；值非空则 header 名必填）─
PLANNING_AGENT_MODEL_TOKEN=<token 值>
PLANNING_AGENT_MODEL_TOKEN_HEADER=token           # 或 X-Bank-Token 等
PLANNING_AGENT_MODEL_USER_ID=<用户 ID>
PLANNING_AGENT_MODEL_USER_ID_HEADER=userId        # 或 X-User-Id 等
PLANNING_AGENT_MODEL_EXTRA_HEADERS=               # JSON 字符串，可选

# ── Agent 身份与限制（含空格值加引号）────────────────
DPA_AGENT_ID=edp_agent
DPA_AGENT_NAME="EDP Agent"
DPA_MAX_ITERATIONS=30

# ── Versatile 工作流结果节点名 ──────────────────────
va_workflow_result_node=GXZQAResponseNode
```

### E.2 `versatile_adapter.env` —— VA 配置

> **注意：所有字段必须带 `ADAPTER_*` 或 `VERSATILE_*` 前缀**。`config.py` 字段名是 `adapter_log_level` / `versatile_timeout`，pydantic 按字段名匹配环境变量。

```env
# ── 服务名 ─────────────────────────────────────────
ADAPTER_APP_NAME=VersatileAdapter

# ── Versatile 平台 URL（含占位符）──────────────────
VERSATILE_URL_TEMPLATE=https://versatile.corp.com/v1/{project_id}/agents/{agent_id}/conversations/{conversation_id}
VERSATILE_TIMEOUT=600

# ── FastAPI ───────────────────────────────────────
ADAPTER_FASTAPI_HOST=0.0.0.0
ADAPTER_FASTAPI_PORT=8091
ADAPTER_FASTAPI_DEBUG=False
ADAPTER_FASTAPI_WORKERS=1

# ── 日志 ──────────────────────────────────────────
ADAPTER_LOG_LEVEL=INFO
ADAPTER_LOG_FILE=logs/versatile_adapter.log
```

### E.3 企业网关自定义 Header 示例

**通用 token/userId 网关：**
```env
PLANNING_AGENT_MODEL_TOKEN=my-token
PLANNING_AGENT_MODEL_TOKEN_HEADER=token
PLANNING_AGENT_MODEL_USER_ID=user-001
PLANNING_AGENT_MODEL_USER_ID_HEADER=userId
```

**银行网关（X-Bank-Token + X-User-Id + 附加渠道号）：**
```env
PLANNING_AGENT_MODEL_TOKEN=gw-token-001
PLANNING_AGENT_MODEL_TOKEN_HEADER=X-Bank-Token
PLANNING_AGENT_MODEL_USER_ID=cust-1001
PLANNING_AGENT_MODEL_USER_ID_HEADER=X-User-Id
PLANNING_AGENT_MODEL_EXTRA_HEADERS={"X-Channel-Id":"mobile-banking"}
```

### E.4 Redis 地址快速选择表

| 场景 | `REDIS_HOST` 值 |
|---|---|
| 远程 Redis 集群 | `<IP 或 DNS>` |
| Docker 容器内访问宿主机 Redis（Linux） | `host.docker.internal` 或 `172.17.0.1` |
| Docker 同网络访问 Redis 容器 | `<redis 容器名>` |
| 独立安装本机 Redis | `127.0.0.1` |

---

## F. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| **启动报 `unknown command 'HELLO'`** | **Redis 版本 < 6.0**（最常见） | 升级 Redis 到 ≥ 6.0，或切换到兼容 RESP3 的 Redis 集群 |
| **启动报 `ModuleNotFoundError: No module named 'agents.EDPAgent.agent'`** | 复制 EDPAgent 代码时 **rsync 末尾斜杠漏了**，`agent.py` 被嵌到了 `EDPAgent/EDPAgent/` 子目录 | `ls .../agents/EDPAgent/` 检查结构，看到 `EDPAgent/` 就重做 §A.3：`rsync .../EDPAgent/ $TARGET/`（两边都带 `/`） |
| 启动报 `adapter_log_level is None` | VA 配置字段漏了 `ADAPTER_` 前缀 | 重新从 `.env.example` 复制，保持字段名不改 |
| 启动报 `A2A: command not found` 或 `Agent: command not found` | `.env` 含空格值没加引号 | 改为 `APP_NAME="A2A Service"` |
| `docker logs` 看到 settings 全 `None` | `.env` 文件未被 pydantic 读到 | 确认文件路径、命名、挂载（Docker 方式） |
| 启动进程反复退出 | 某个 Python 进程起不来 | `docker logs edpagent`（Docker 方式）或终端 stderr（独立方式）找根因 |
| 端口冲突 | 8090 / 8091 被占 | Docker：`./run.sh --port-a2a 18090 --port-va 18091`；独立：改 `.env` 里端口 |
| LLM 调用 SSL 错误 | 企业自签证书 | 设 `SKILL_LLM_TLS_VERIFY=false` |
| 自定义 header 启动报错 | `_TOKEN` 有值但 `_TOKEN_HEADER` 未配 | 成对填写，或两个都留空 |
| Windows 编辑 .env 后 Linux 启动异常 | CRLF 换行符 | `sed -i 's/\r$//' config/*.env`（Linux）或 VS Code 切 LF |
| `docker load` 报 `no space left` | 镜像 1.24 GB，磁盘不足 | 清理或换目录（镜像解压后会占 2~3 GB 额外空间） |
| 容器内访问 `host.docker.internal:6379` 连接拒绝 | Redis 容器绑到 `127.0.0.1:6379`，容器从 `172.17.0.1` 访问不到 | Redis 改为 `-p 6379:6379`（监听所有接口），或 Redis 与 edpagent 加入同一 docker network，`REDIS_HOST=<redis 容器名>` |
| tiktoken 报 `Could not connect to huggingface/openai` | 离线环境没有预置 cl100k_base | 确认 `TIKTOKEN_CACHE_DIR` 指向放了 SHA1 文件的目录（Docker 方式已内置） |

---

## 参考资料

- AgentRule 六规则与决策规则：[`../AgentRule.md`](../AgentRule.md)

---

*基线版本：agent-runtime @ `origin/feature/procode_enhancement`，agent-store @ `origin/EDP-agent`（含 MCP 工具链 `tool/call_mcp.py` + `rail/mcp_interrupt_rail.py` 与第 4 个理财交互式重构 skill `rebuild_interact_finance_rec_skill/`）*
*文档日期：2026-04-25*
