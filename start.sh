#!/usr/bin/env bash
# Production entrypoint (Replit Reserved VM or any Linux host).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build)
fi
cd backend
# Only trust X-Forwarded-* from known proxy addresses. Trusting "*" would let any client spoof its IP.
# Set FORWARDED_ALLOW_IPS to your host's proxy range if you need real client IPs; login throttling also has a
# global cap, so it stays effective even when every request appears to come from the proxy.
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
