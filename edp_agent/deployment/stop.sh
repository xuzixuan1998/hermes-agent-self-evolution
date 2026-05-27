#!/usr/bin/env bash
#
# stop.sh — 停止 edpagent 容器
#
set -euo pipefail

NAME="${1:-edpagent}"

if docker ps -a --format '{{.Names}}' | grep -q "^${NAME}$"; then
    echo "停止并删除容器 $NAME"
    docker rm -f "$NAME" >/dev/null
    echo "✅ 已停止"
else
    echo "容器 $NAME 不存在"
fi
