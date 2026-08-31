#!/usr/bin/env bash
# ==============================================================================
# 🚀 TechB NShop Rank Multi-Worker Launcher
# ==============================================================================
# 사용법:
#   ./start_worker.sh [쓰레드수]
# 예시:
#   ./start_worker.sh 4
#   ./start_worker.sh 8
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 기본값: 4쓰레드 (권장: 4 또는 8)
THREADS="${1:-4}"

# 화면 환경변수 자동 감지
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# 기존 포트 및 좀비 크롬 정리
fuser -k 9201/tcp 9202/tcp 9203/tcp 9204/tcp 9205/tcp 9206/tcp 9207/tcp 9208/tcp 2>/dev/null || true
pkill -f "/usr/bin/google-chrome.*remote-debugging-port" 2>/dev/null || true
sleep 0.5

# Python 실행 경로 감지
if [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
elif [ -f "/home/tech/venv/bin/python3" ]; then
    PYTHON_BIN="/home/tech/venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

echo "================================================================================"
echo "🚀 [TechB Crawler] Multi-Worker Starting (Threads: $THREADS, Display: $DISPLAY / $WAYLAND_DISPLAY)"
echo "================================================================================"

exec "$PYTHON_BIN" main.py worker --threads "$THREADS"
