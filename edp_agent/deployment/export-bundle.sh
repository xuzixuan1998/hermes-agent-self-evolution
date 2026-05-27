#!/usr/bin/env bash
#
# export-bundle.sh — 把镜像和部署文件打包为可传输的离线安装包
#
# 产物：./bundle/edpagent-offline-<日期>.tar.gz
#   ├── edpagent.image.tar    docker save 出的镜像（离线 load 用）
#   ├── config/
#   │   ├── a2a_service.env
#   │   └── versatile_adapter.env
#   ├── import-bundle.sh      客户侧用于 docker load + 启动
#   └── README.md
#
# 使用：./export-bundle.sh [agent-runtime-root]
#
set -euo pipefail

BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RUNTIME="${1:-${AGENT_RUNTIME:-$HOME/EDPAgent/agent-runtime}}"
IMAGE_TAG="${EDPAGENT_IMAGE_TAG:-edpagent:latest}"
BUNDLE_DIR="$BUILD_DIR/bundle"
STAMP="$(date +%Y%m%d-%H%M%S)"
BUNDLE_NAME="edpagent-offline-$STAMP"
STAGE_DIR="$BUNDLE_DIR/$BUNDLE_NAME"
A2A_ENV_SRC="$AGENT_RUNTIME/applications/a2a_service/.env.example"
VA_ENV_SRC="$AGENT_RUNTIME/applications/versatile_adapter/.env.example"
A2A_ENV_FALLBACK="$BUILD_DIR/config/a2a_service.env"
VA_ENV_FALLBACK="$BUILD_DIR/config/versatile_adapter.env"

copy_env_file() {
  local src="$1"
  local fallback="$2"
  local dst="$3"

  if [ -f "$src" ]; then
    cp "$src" "$dst"
    return
  fi
  if [ -f "$fallback" ]; then
    cp "$fallback" "$dst"
    return
  fi
  echo "❌ 未找到配置源：$src"
  echo "   也未找到已生成配置：$fallback"
  echo "   请先运行 ./build.sh，或执行：./export-bundle.sh /path/to/agent-runtime"
  exit 1
}

# ── 校验镜像存在 ─────────────────────────────────────────────────────
docker image inspect "$IMAGE_TAG" >/dev/null 2>&1 \
  || { echo "❌ 镜像 $IMAGE_TAG 不存在，请先运行 ./build.sh"; exit 1; }

echo "镜像：$IMAGE_TAG"
echo "输出：$BUNDLE_DIR/$BUNDLE_NAME.tar.gz"
echo "配置源：$AGENT_RUNTIME/applications/{a2a_service,versatile_adapter}/.env.example"
echo ""

# ── 1. 清理并建 stage 目录 ────────────────────────────────────────────
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR/config"

# ── 2. 导出镜像 ─────────────────────────────────────────────────────
echo "[1/3] docker save → edpagent.image.tar"
docker save "$IMAGE_TAG" -o "$STAGE_DIR/edpagent.image.tar"
echo "     $(du -h "$STAGE_DIR/edpagent.image.tar" | cut -f1)"

# ── 3. 拷贝部署文件 ─────────────────────────────────────────────────
echo "[2/3] 拷贝部署文件"
copy_env_file "$A2A_ENV_SRC" "$A2A_ENV_FALLBACK" "$STAGE_DIR/config/a2a_service.env"
copy_env_file "$VA_ENV_SRC" "$VA_ENV_FALLBACK" "$STAGE_DIR/config/versatile_adapter.env"
cp "$BUILD_DIR/import-bundle.sh" "$STAGE_DIR/"
cp "$BUILD_DIR/run.sh"  "$STAGE_DIR/"
cp "$BUILD_DIR/stop.sh" "$STAGE_DIR/"
chmod +x "$STAGE_DIR/import-bundle.sh" "$STAGE_DIR/run.sh" "$STAGE_DIR/stop.sh"

# 用完整部署指南作为 README（单一来源，避免双份维护）
DEPLOYMENT_MD="$(cd "$BUILD_DIR/.." && pwd)/docs/deployment.md"
if [ ! -f "$DEPLOYMENT_MD" ]; then
    echo "❌ 未找到部署指南: $DEPLOYMENT_MD"
    echo "   请确认 docs/deployment.md 存在后重试"
    exit 1
fi
cp "$DEPLOYMENT_MD" "$STAGE_DIR/README.md"
echo "     拷贝 docs/deployment.md → README.md ($(wc -l < "$DEPLOYMENT_MD") 行)"

# ── 4. 打包 ─────────────────────────────────────────────────────────
echo "[3/3] 打包 tar.gz"
cd "$BUNDLE_DIR"
tar czf "$BUNDLE_NAME.tar.gz" "$BUNDLE_NAME"

FINAL_PATH="$BUNDLE_DIR/$BUNDLE_NAME.tar.gz"
echo ""
echo "✅ 离线包生成完成"
echo "   路径：$FINAL_PATH"
echo "   大小：$(du -h "$FINAL_PATH" | cut -f1)"
echo ""
echo "将 $FINAL_PATH 传输到客户机器，解压后执行 ./import-bundle.sh"
