#!/usr/bin/env bash
#
# import-bundle.sh — 在客户侧离线环境中导入镜像
#
# 前置：
#   - 已安装 Docker（≥ 20.10）
#   - 已解压 bundle，并 cd 到 bundle 目录
#
# 执行：./import-bundle.sh
#
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_TAR="$BUNDLE_DIR/edpagent.image.tar"

[ -f "$IMAGE_TAR" ] || { echo "❌ 未找到 edpagent.image.tar"; exit 1; }

echo "[1/2] docker load < edpagent.image.tar"
docker load -i "$IMAGE_TAR"

echo ""
echo "[2/2] 校验镜像"
docker images edpagent --format "   {{.Repository}}:{{.Tag}}   {{.Size}}"

echo ""
echo "✅ 镜像已导入。"
echo ""
echo "下一步："
echo "  1. 按企业环境检查并编辑 config/a2a_service.env"
echo "  2. 按企业环境检查并编辑 config/versatile_adapter.env"
echo "     注意：Docker 单容器模式下 REDIS_HOST 不能填 localhost/127.0.0.1"
echo "  3. ./run.sh                   # 启动单容器"
echo "  4. curl http://localhost:8090/health   # 业务端到端测试请咨询现场同事"
