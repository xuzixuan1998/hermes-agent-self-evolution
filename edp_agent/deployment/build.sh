#!/usr/bin/env bash
#
# build.sh — 在连网环境中构建 edpagent Docker 镜像
#
# 工作流程：
#   1. 从 agent-runtime 取框架代码（a2a_service + versatile_adapter）
#   2. 把 agent-store 中的 EDPAgent 合并到 a2a_service/agents/EDPAgent/
#   3. 基于 Dockerfile 构建镜像 edpagent:latest
#
# 使用：
#   ./build.sh                                       # 默认路径
#   ./build.sh /path/to/agent-runtime /path/to/agent-store
#
set -euo pipefail

# ── 参数与路径 ─────────────────────────────────────────────────────────
AGENT_RUNTIME="${1:-$HOME/EDPAgent/agent-runtime}"
AGENT_STORE="${2:-$HOME/EDPAgent/agent-store}"
BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTEXT_DIR="$BUILD_DIR/.build-context"
IMAGE_TAG="${EDPAGENT_IMAGE_TAG:-edpagent:latest}"
CONFIG_DIR="$BUILD_DIR/config"
A2A_ENV_SRC="$AGENT_RUNTIME/applications/a2a_service/.env.example"
VA_ENV_SRC="$AGENT_RUNTIME/applications/versatile_adapter/.env.example"
A2A_ENV_DST="$CONFIG_DIR/a2a_service.env"
VA_ENV_DST="$CONFIG_DIR/versatile_adapter.env"

# ── 校验输入 ──────────────────────────────────────────────────────────
[ -d "$AGENT_RUNTIME/applications/a2a_service" ] \
  || { echo "❌ 未找到 agent-runtime: $AGENT_RUNTIME/applications/a2a_service"; exit 1; }
[ -d "$AGENT_RUNTIME/applications/versatile_adapter" ] \
  || { echo "❌ 未找到 versatile_adapter: $AGENT_RUNTIME/applications/versatile_adapter"; exit 1; }
[ -d "$AGENT_STORE/community/EDPAgent" ] \
  || { echo "❌ 未找到 EDPAgent: $AGENT_STORE/community/EDPAgent"; exit 1; }
[ -f "$A2A_ENV_SRC" ] \
  || { echo "❌ 未找到 a2a_service 配置模板: $A2A_ENV_SRC"; exit 1; }
[ -f "$VA_ENV_SRC" ] \
  || { echo "❌ 未找到 versatile_adapter 配置模板: $VA_ENV_SRC"; exit 1; }

# 运行容器需要 config/*.env；默认保留已填写配置，设置 EDPAGENT_REFRESH_ENV=1 可刷新。
mkdir -p "$CONFIG_DIR"
if [ ! -f "$A2A_ENV_DST" ] || [ "${EDPAGENT_REFRESH_ENV:-0}" = "1" ]; then
  cp "$A2A_ENV_SRC" "$A2A_ENV_DST"
fi
if [ ! -f "$VA_ENV_DST" ] || [ "${EDPAGENT_REFRESH_ENV:-0}" = "1" ]; then
  cp "$VA_ENV_SRC" "$VA_ENV_DST"
fi

echo "✅ agent-runtime:        $AGENT_RUNTIME"
echo "✅ agent-store:          $AGENT_STORE"
echo "✅ build context:        $CONTEXT_DIR"
echo "✅ image tag:            $IMAGE_TAG"
echo "✅ a2a env:              $A2A_ENV_DST"
echo "✅ versatile env:        $VA_ENV_DST"
echo ""

# ── 1. 准备 build context ──────────────────────────────────────────────
echo "[1/3] 准备 build 上下文"
rm -rf "$CONTEXT_DIR"
mkdir -p "$CONTEXT_DIR"

rsync -a --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='logs/' --exclude='.venv/' --exclude='*.egg-info/' \
  "$AGENT_RUNTIME/applications/a2a_service/" "$CONTEXT_DIR/a2a_service/"

rsync -a --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='logs/' --exclude='.venv/' --exclude='*.egg-info/' \
  "$AGENT_RUNTIME/applications/versatile_adapter/" "$CONTEXT_DIR/versatile_adapter/"

# ── 2. 合并 EDPAgent 业务代码 ──────────────────────────────────────────
echo "[2/3] 合并 EDPAgent 业务代码到 a2a_service/agents/EDPAgent/"
rm -rf "$CONTEXT_DIR/a2a_service/agents/EDPAgent"
mkdir -p "$CONTEXT_DIR/a2a_service/agents/EDPAgent"

rsync -a --exclude='__pycache__/' --exclude='*.pyc' \
  --exclude='docs/' --exclude='deployment/' \
  "$AGENT_STORE/community/EDPAgent/" \
  "$CONTEXT_DIR/a2a_service/agents/EDPAgent/"

# 清理可能被 copy 进来的本地 .env（避免把开发环境凭证烘进镜像）
find "$CONTEXT_DIR" -maxdepth 4 -name ".env" -type f -delete 2>/dev/null || true

# 拷贝 Dockerfile 和 entrypoint.sh 进 context
cp "$BUILD_DIR/Dockerfile" "$CONTEXT_DIR/Dockerfile"
cp "$BUILD_DIR/entrypoint.sh" "$CONTEXT_DIR/entrypoint.sh"
chmod +x "$CONTEXT_DIR/entrypoint.sh"

# ── 3. 构建镜像 ───────────────────────────────────────────────────────
echo "[3/3] docker build -t $IMAGE_TAG"
docker build -t "$IMAGE_TAG" "$CONTEXT_DIR"

echo ""
echo "✅ 镜像构建完成：$IMAGE_TAG"
docker images "$IMAGE_TAG" --format "   {{.Repository}}:{{.Tag}}   {{.Size}}   {{.CreatedSince}}"
echo ""
echo "下一步：./export-bundle.sh  → 导出离线安装包"
