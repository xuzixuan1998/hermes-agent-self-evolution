#!/bin/bash
#
# entrypoint.sh — 容器内管理 versatile_adapter + a2a_service 两个 Python 进程
#
# 设计：
#   1. 启动顺序：先 VA，轮询 /health 确认就绪，再 a2a
#   2. 信号处理：SIGTERM/INT → 转发给两个子进程，等它们退出
#   3. 任一子进程退出 → 容器整体退出（由 Docker --restart 拉起）
#   4. PID 1 zombie 回收由 Docker --init（tini）负责
#
set -e

VA_PID=""
A2A_PID=""

cleanup() {
    echo "[entrypoint] 收到停止信号，正在关闭子进程"
    [ -n "$A2A_PID" ] && kill -TERM "$A2A_PID" 2>/dev/null || true
    [ -n "$VA_PID" ]  && kill -TERM "$VA_PID"  2>/dev/null || true
    # 最多等 10 秒优雅退出
    for i in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$A2A_PID" 2>/dev/null || kill -0 "$VA_PID" 2>/dev/null || break
        sleep 1
    done
    # 还没死就强杀
    [ -n "$A2A_PID" ] && kill -KILL "$A2A_PID" 2>/dev/null || true
    [ -n "$VA_PID" ]  && kill -KILL "$VA_PID"  2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# ── 1. 启动 versatile-adapter ──────────────────────────────────────
echo "[entrypoint] 启动 versatile-adapter，配置 $VA_CONFIG"
cd /app/versatile_adapter
# source .env 到当前子 shell，pydantic-settings 会从 os.environ 读取
(
python main.py
) &
VA_PID=$!
echo "[entrypoint] versatile-adapter PID=$VA_PID"

# 等 VA 健康（最多 60 秒）
echo -n "[entrypoint] 等待 versatile-adapter 就绪 "
VA_READY=0
for i in $(seq 1 60); do
    if curl -fs http://localhost:8091/health >/dev/null 2>&1; then
        echo "✓"
        VA_READY=1
        break
    fi
    # 如果 VA 已经崩了，提前退出
    if ! kill -0 "$VA_PID" 2>/dev/null; then
        echo "✗ versatile-adapter 进程已退出"
        wait "$VA_PID" 2>/dev/null || true
        exit 1
    fi
    echo -n "."
    sleep 1
done
if [ "$VA_READY" -ne 1 ]; then
    echo "✗ versatile-adapter 60 秒内未就绪"
    cleanup
fi

# ── 2. 启动 a2a-service ────────────────────────────────────────────
echo "[entrypoint] 启动 a2a-service，配置 $A2A_CONFIG"
cd /app/a2a_service
(
python main.py
) &
A2A_PID=$!
echo "[entrypoint] a2a-service PID=$A2A_PID"

# ── 3. 守护：任一子进程退出就整容器退出 ─────────────────────────────
wait -n "$VA_PID" "$A2A_PID"
EXIT_CODE=$?
echo "[entrypoint] 有子进程退出（exit=$EXIT_CODE），关闭容器"
cleanup
