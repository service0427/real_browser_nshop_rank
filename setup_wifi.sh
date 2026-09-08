#!/usr/bin/env bash
# ==============================================================================
# 📶 TechB Android Wi-Fi Batch Setup Wrapper
# ==============================================================================
# 사용법:
#   ./setup_wifi.sh "와이파이SSID" "비밀번호"
#   ./setup_wifi.sh (인자 없이 실행 시 대화형으로 SSID/비번 입력)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SSID="$1"
PASSWORD="$2"

if [ -z "$SSID" ]; then
    echo "================================================================================"
    echo "📶 [TechB] 안드로이드 실기기 Wi-Fi 일괄 설정"
    echo "================================================================================"
    read -p "▶ 연결할 Wi-Fi SSID(이름)을 입력하세요: " SSID
    read -p "▶ Wi-Fi 비밀번호를 입력하세요 (없으면 엔터): " PASSWORD
    echo ""
fi

if [ -z "$SSID" ]; then
    echo "❌ [오류] Wi-Fi SSID가 입력되지 않았습니다. 종료합니다."
    exit 1
fi

# Python 경로 감지
if [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
    PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
elif [ -f "/home/tech/venv/bin/python3" ]; then
    PYTHON_BIN="/home/tech/venv/bin/python3"
else
    PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/setup_wifi.py" --ssid "$SSID" --password "$PASSWORD"
