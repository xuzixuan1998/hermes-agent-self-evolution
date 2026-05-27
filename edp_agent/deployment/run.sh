#!/usr/bin/env bash
#
# run.sh — 单容器方案启动（只需 docker，不需 compose）
#
# 参数：
#   --name NAME       容器名，默认 edpagent
#   --image IMAGE     镜像 tag，默认 edpagent:latest
#   --port-a2a PORT   a2a_service 对外端口，默认 8090
#   --port-va PORT    versatile_adapter 对外端口，默认 8091
#
set -euo pipefail

BUILD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAME="edpagent"
IMAGE="${EDPAGENT_IMAGE_TAG:-edpagent:latest}"
PORT_A2A=18001
PORT_VA=8091

while [ $# -gt 0 ]; do
    case "$1" in
        --name)     NAME="$2"; shift 2 ;;
        --image)    IMAGE="$2"; shift 2 ;;
        --port-a2a) PORT_A2A="$2"; shift 2 ;;
        --port-va)  PORT_VA="$2"; shift 2 ;;
        -h|--help)
            echo "用法：$0 [--name NAME] [--image IMAGE] [--port-a2a PORT] [--port-va PORT]"
            exit 0
            ;;
        *) echo "❌ 未知参数：$1"; exit 1 ;;
    esac
done

VA_ENV="$BUILD_DIR/config/versatile_adapter.env"
A2A_ENV="$BUILD_DIR/config/a2a_service.env"

read_env_value() {
    local key="$1"
    local file="$2"
    local value=""
    value="$(grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)"
    value="${value%$'\r'}"
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    printf '%s' "$value"
}

# ── 校验 ─────────────────────────────────────────────────────────────
docker image inspect "$IMAGE" >/dev/null 2>&1 \
  || { echo "❌ 镜像 $IMAGE 不存在，请先 ./import-bundle.sh 或 ./build.sh"; exit 1; }
[ -f "$VA_ENV" ]  || { echo "❌ 未找到 $VA_ENV（请先 ./build.sh 生成，或使用离线包自带 config/*.env）"; exit 1; }
[ -f "$A2A_ENV" ] || { echo "❌ 未找到 $A2A_ENV（请先 ./build.sh 生成，或使用离线包自带 config/*.env）"; exit 1; }

REDIS_HOST_VALUE="$(read_env_value REDIS_HOST "$A2A_ENV")"
case "$REDIS_HOST_VALUE" in
    ""|localhost|127.0.0.1)
        echo "❌ 当前 REDIS_HOST=${REDIS_HOST_VALUE:-<空>} 不适合单容器 Docker 运行。"
        echo "   镜像内没有 Redis，localhost/127.0.0.1 会指向 edpagent 容器自身。"
        echo "   请在 $A2A_ENV 中改为可从容器访问的 Redis 地址，例如："
        echo "   - 宿主机 Redis：REDIS_HOST=host.docker.internal"
        echo "   - 同一 docker network 的 Redis 容器：REDIS_HOST=<redis 容器名>"
        echo "   - 远程 Redis：REDIS_HOST=<Redis IP 或 DNS>"
        exit 1
        ;;
esac

# ── 清理旧容器 ───────────────────────────────────────────────────────
if docker ps -a --format '{{.Names}}' | grep -q "^${NAME}$"; then
    echo "[run] 删除已有容器 $NAME"
    docker rm -f "$NAME" >/dev/null
fi

mkdir -p "$BUILD_DIR/logs/a2a_service" "$BUILD_DIR/logs/versatile_adapter"

# ── 启动 ────────────────────────────────────────────────────────────
echo "[run] 启动容器 $NAME（镜像 $IMAGE）"
docker run -d \
    --name "$NAME" \
    --init \
    --add-host "host.docker.internal:host-gateway" \
    -p "${PORT_A2A}:8090" -p "${PORT_VA}:8091" \
    --env-file "$VA_ENV" \
    --env-file "$A2A_ENV" \
    -v "$BUILD_DIR/logs/a2a_service:/app/a2a_service/logs" \
    -v "$BUILD_DIR/logs/versatile_adapter:/app/versatile_adapter/logs" \
    --restart unless-stopped \
    --health-cmd="curl -fs http://localhost:8090/health && curl -fs http://localhost:8091/health" \
    --health-interval=60s \
    --health-timeout=3s \
    --health-retries=3 \
    --health-start-period=60s \
    "$IMAGE" >/dev/null

# ── 等健康 ───────────────────────────────────────────────────────────
echo -n "[run] 等待容器变 healthy（最多 60 秒）"
for i in $(seq 1 60); do
    status=$(docker inspect -f '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo unknown)
    [ "$status" = "healthy" ] && { echo "✓"; break; }
    echo -n "."
    sleep 1
done

if [ "$status" != "healthy" ]; then
    echo "✗（最终状态：$status）"
    echo
    echo "查日志："
    echo "   docker logs $NAME | tail -60"
    exit 1
fi

echo ""
echo "✅ 启动完成"
docker ps --filter "name=${NAME}" --format "   {{.Names}}   {{.Status}}   {{.Ports}}"
echo ""
echo "验证："
echo "   curl http://localhost:${PORT_A2A}/health"
echo "   curl http://localhost:${PORT_VA}/health"
echo "   (业务端到端功能测试请咨询现场同事)"
echo ""
echo "查日志：          docker logs -f $NAME"
echo "进容器看进程：    docker exec $NAME ps -ef"
echo "停止：            ./stop.sh"
