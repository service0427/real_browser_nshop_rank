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

# OS 및 로케일 한국어 강제 설정 (브라우저 번역 팝업 방지)
export LANG="ko_KR.UTF-8"
export LC_ALL="ko_KR.UTF-8"
export LANGUAGE="ko_KR:ko"
export PYTHONUNBUFFERED=1

# ------------------------------------------------------------------------------
# 📱 안드로이드 실기기(폰) 연결 상태 자동 감지 및 쓰레드/스테이지 결정
# ------------------------------------------------------------------------------
CONNECTED_PHONES=0
UNAUTH_PHONES=0

if command -v adb >/dev/null 2>&1; then
    CONNECTED_PHONES=$(adb devices 2>/dev/null | grep -v "List of" | grep -w "device" | wc -l)
    UNAUTH_PHONES=$(adb devices 2>/dev/null | grep -w "unauthorized" | wc -l)
fi

if [ "$UNAUTH_PHONES" -gt 0 ]; then
    echo "⚠️  [주의] USB 디버깅 미승인 기기 ${UNAUTH_PHONES}대 감지! 폰 화면에서 '이 컴퓨터에서 항상 허용'을 눌러주세요."
fi

if [ "$1" == "dual" ] || [ "$1" == "all" ]; then
    STAGE="dual"
    PC_THREADS="${2:-4}"
    MOBILE_THREADS="${3:-10}"
elif [ -n "$1" ]; then
    # 사용자가 명시적으로 인자를 입력한 경우 (예: ./start_worker.sh 4 2)
    THREADS="$1"
    STAGE="${2:-3}"
else
    # 인자 없이 ./start_worker.sh 만 실행한 경우 자동 결정
    if [ "$CONNECTED_PHONES" -gt 0 ]; then
        STAGE=3
        THREADS="$CONNECTED_PHONES"
        echo "📱 [기기 자동 감지] 연결된 실기기 ${CONNECTED_PHONES}대 감지 -> Stage 3 (실기기 폰 모드, ${THREADS}쓰레드) 자동 가동"
    else
        STAGE=2
        THREADS=4
        echo "🖥️ [기기 자동 감지] 연결된 안드로이드 기기 없음 -> Stage 2 (PC 브라우저 모드, ${THREADS}쓰레드) 자동 가동"
    fi
fi

# 화면 환경변수 자동 감지 (Wayland / X11 / XAUTHORITY)
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

MUTTER_AUTH=$(ls -t "/run/user/$(id -u)/.mutter-Xwaylandauth."* 2>/dev/null | head -n 1)
if [ -n "$MUTTER_AUTH" ]; then
    export XAUTHORITY="$MUTTER_AUTH"
elif [ -f "$HOME/.Xauthority" ]; then
    export XAUTHORITY="$HOME/.Xauthority"
fi

xhost +local: 2>/dev/null || true

# 24시간 화면 켜짐 유지 (화면 꺼짐 및 스크린세이버 방지)
xset s off -dpms 2>/dev/null || true
xset s noblank 2>/dev/null || true
gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null || true

# 해당 스테이지에 맞는 포트 및 프로세스만 격리 정리
if [ "$STAGE" == "dual" ]; then
    fuser -k 9201/tcp 9202/tcp 9203/tcp 9204/tcp 9205/tcp 9206/tcp 9207/tcp 9208/tcp 2>/dev/null || true
    pkill -f "/usr/bin/google-chrome.*remote-debugging-port=920" 2>/dev/null || true
    for p in $(seq 9301 9320); do fuser -k "$p/tcp" 2>/dev/null || true; done
elif [ "$STAGE" -eq 2 ]; then
    fuser -k 9201/tcp 9202/tcp 9203/tcp 9204/tcp 9205/tcp 9206/tcp 9207/tcp 9208/tcp 2>/dev/null || true
    pkill -f "/usr/bin/google-chrome.*remote-debugging-port=920" 2>/dev/null || true
elif [ "$STAGE" -eq 3 ]; then
    for p in $(seq 9301 9320); do fuser -k "$p/tcp" 2>/dev/null || true; done
fi
sleep 0.5

# Python 실행 경로 감지
if [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
elif [ -f "/home/tech/venv/bin/python3" ]; then
    PYTHON_BIN="/home/tech/venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

# ------------------------------------------------------------------------------
# 📶 [Wi-Fi 검증 및 실행]
# ------------------------------------------------------------------------------
WIFI_SSID="${WIFI_SSID:-tech_mik}"
WIFI_PW="${WIFI_PW:-13241324}"

if [ "$STAGE" == "dual" ]; then
    echo "================================================================================"
    echo "📶 [Wi-Fi 검증] 실기기 ${WIFI_SSID} / ${WIFI_PW} 연결 상태 확인 및 자동 연결..."
    echo "================================================================================"
    "$PYTHON_BIN" "$SCRIPT_DIR/scripts/setup_wifi.py" --ssid "$WIFI_SSID" --password "$WIFI_PW" || true

    echo "================================================================================"
    echo "🚀 [TechB Crawler] Dual Mode Starting (PC: ${PC_THREADS}개, Mobile: ${MOBILE_THREADS}개)"
    echo "================================================================================"
    mkdir -p "$SCRIPT_DIR/services/runtime/logs"
    "$PYTHON_BIN" main.py worker --threads "$PC_THREADS" --stage 2 > "$SCRIPT_DIR/services/runtime/logs/worker_pc.log" 2>&1 &
    PC_PID=$!
    echo "🖥️ [PC Stage 2] 백그라운드 가동 (PID: $PC_PID, 로그: services/runtime/logs/worker_pc.log)"

    trap "kill $PC_PID 2>/dev/null || true; exit" SIGINT SIGTERM EXIT

    exec "$PYTHON_BIN" main.py worker --threads "$MOBILE_THREADS" --stage 3
elif [ "$STAGE" -eq 3 ]; then
    echo "================================================================================"
    echo "📶 [Wi-Fi 검증] 실기기 ${WIFI_SSID} / ${WIFI_PW} 연결 상태 확인 및 자동 연결 수행..."
    echo "================================================================================"
    "$PYTHON_BIN" "$SCRIPT_DIR/scripts/setup_wifi.py" --ssid "$WIFI_SSID" --password "$WIFI_PW" || true

    echo "================================================================================"
    echo "🚀 [TechB Crawler] Multi-Worker Starting (Threads: $THREADS, Stage: $STAGE, Display: $DISPLAY)"
    echo "================================================================================"
    exec "$PYTHON_BIN" main.py worker --threads "$THREADS" --stage "$STAGE"
else
    echo "================================================================================"
    echo "🚀 [TechB Crawler] Multi-Worker Starting (Threads: $THREADS, Stage: $STAGE, Display: $DISPLAY)"
    echo "================================================================================"
    exec "$PYTHON_BIN" main.py worker --threads "$THREADS" --stage "$STAGE"
fi
