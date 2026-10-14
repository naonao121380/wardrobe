#!/usr/bin/env bash
# 容器入口：api 会先跑迁移再起服务；cli 直接透传给运维命令行。
set -euo pipefail

cmd="${1:-api}"

case "$cmd" in
  api)
    echo "[entrypoint] 执行数据库迁移 alembic upgrade head"
    alembic upgrade head
    echo "[entrypoint] 启动 uvicorn"
    exec uvicorn app.main:app \
      --host 0.0.0.0 \
      --port 8000 \
      --proxy-headers \
      --forwarded-allow-ips='*'
    ;;
  cli)
    shift
    exec python -m app.cli "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
