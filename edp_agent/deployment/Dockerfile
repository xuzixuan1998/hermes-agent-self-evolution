# ──────────────────────────────────────────────────────────────────────
# EDPAgent 运行镜像
#   同时作为 a2a_service (8090) 和 versatile_adapter (8091) 的运行环境
#   两个服务用同一镜像不同 WORKDIR + 不同 command 启动
# ──────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# 基本工具：
#   curl        —— 健康检查 + entrypoint 轮询 /health
#   vim less    —— 容器内查看/编辑配置与日志
#   procps      —— ps / top / free（排查进程与内存）
#   iproute2    —— ss / ip（看端口与路由）
#   net-tools   —— netstat（老习惯用户）
#   iputils-ping—— ping（网络联通性）
#   dnsutils    —— dig / nslookup（DNS 排障）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        vim \
        less \
        procps \
        iproute2 \
        net-tools \
        iputils-ping \
        dnsutils && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 安装 uv 加速 pip
RUN pip install --no-cache-dir uv

# 建虚拟环境 + 装依赖（离线化的关键：所有包必须在 build 时拉齐）
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 注：openjiuwen 0.1.12 依赖 a2a-sdk==1.0.0，和本项目要求 1.0.0一致。
RUN uv venv $VIRTUAL_ENV && \
    uv pip install --no-cache --prerelease=allow \
        'openjiuwen==0.1.12' \
        'pydantic>=2.7.0' \
        'pydantic-settings>=2.3.0' \
        'loguru>=0.7.0' \
        'aiosqlite>=0.19.0' \
        'sse-starlette>=3.3.0' \
        'fastapi>=0.111.0' \
        'uvicorn[standard]>=0.30.0' \
        'httpx>=0.27.0' \
        'redis>=5.0.0' \
        'protobuf>=4.25.0' \
        'python-dotenv>=1.0' \
        'pyyaml>=6.0' \
        'pycryptodome>=3.20.0' && \
    uv pip install --no-cache --prerelease=allow --reinstall-package a2a-sdk 'a2a-sdk==1.0.0'

# 拷入框架（build 上下文：agent-runtime/applications）
# 业务代码（EDPAgent_refactor）在 build.sh 中已 merge 进 a2a_service/agents/EDPAgent/
COPY a2a_service /app/a2a_service
COPY versatile_adapter /app/versatile_adapter

# 单容器方案：entrypoint.sh 管两个 Python 进程
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 默认行为
ENV USE_FACTORY_MODE=true \
    NO_PROXY=localhost,127.0.0.1

# 暴露两个端口
EXPOSE 8090 8091

# 单一入口：bash 启两进程；PID 1 zombie 回收交给 Docker --init
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
