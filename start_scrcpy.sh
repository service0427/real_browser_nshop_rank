#!/bin/bash
# ==============================================================================
# 📱 start_scrcpy.sh - 실기기 및 폰팜(19대) 통합 실시간 미러링 스크립트
# ==============================================================================
# 사용법:
#   ./start_scrcpy.sh            -> 19대 전체 (실기기 1대 + 폰팜 18대) 그리드 미러링
#   ./start_scrcpy.sh 10         -> 상위 10대만 그리드 미러링
#   ./start_scrcpy.sh real       -> 완전 실기기 (R3CW5084CEW) 1대만 고화질 미러링
#   ./start_scrcpy.sh farm       -> 폰팜 18대만 그리드 미러링
#   ./start_scrcpy.sh farm 10    -> 폰팜 중 상위 10대만 그리드 미러링
#   ./start_scrcpy.sh <시리얼>    -> 지정한 특정 시리얼 기기만 미러링
# ==============================================================================

REAL_PHONE_SERIAL="R3CW5084CEW"
TARGET_MODE="${1:-all}"
MAX_LIMIT=0

# 숫자 파라미터 감지 (예: ./start_scrcpy.sh 10 또는 ./start_scrcpy.sh farm 10)
if [[ "$1" =~ ^[0-9]+$ ]]; then
    TARGET_MODE="all"
    MAX_LIMIT="$1"
elif [ "$1" == "farm" ] && [[ "$2" =~ ^[0-9]+$ ]]; then
    TARGET_MODE="farm"
    MAX_LIMIT="$2"
elif [ "$1" == "all" ] && [[ "$2" =~ ^[0-9]+$ ]]; then
    TARGET_MODE="all"
    MAX_LIMIT="$2"
fi

# GUI 환경변수 자동 바인딩
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

DEVICES=($(adb devices | grep -w "device" | awk '{print $1}'))
TOTAL_COUNT=${#DEVICES[@]}

if [ "$TOTAL_COUNT" -eq 0 ]; then
    echo "❌ 연결된 안드로이드 기기(device)가 없습니다. ADB 연결을 확인해주세요."
    exit 1
fi

echo "================================================================================"
echo "📱 [SOME 3C & Real Phone] scrcpy 통합 미러링 매니저 (감지된 기기: ${TOTAL_COUNT}대)"
echo "================================================================================"

# 1. 단일 실기기 모드 (R3CW5084CEW)
if [ "$TARGET_MODE" == "real" ] || [ "$TARGET_MODE" == "phone" ] || [ "$TARGET_MODE" == "$REAL_PHONE_SERIAL" ]; then
    if ! adb devices | grep -qw "$REAL_PHONE_SERIAL"; then
        echo "❌ 실기기($REAL_PHONE_SERIAL)가 연결되어 있지 않습니다."
        exit 1
    fi
    echo "🚀 [순정 실기기] Galaxy S23+ ($REAL_PHONE_SERIAL) 고화질 미러링 창 실행..."
    scrcpy -s "$REAL_PHONE_SERIAL" \
        --window-title="[순정 실기기] Galaxy S23+ ($REAL_PHONE_SERIAL)" \
        --max-size=1080 \
        --max-fps=60 \
        --stay-awake \
        --no-audio &
    echo "✔ 실기기 미러링이 실행되었습니다. (PID: $!)"
    exit 0
fi

# 2. 특정 시리얼 지정 모드
if [ "$TARGET_MODE" != "all" ] && [ "$TARGET_MODE" != "farm" ]; then
    TARGET_SERIAL="$TARGET_MODE"
    if ! adb devices | grep -qw "$TARGET_SERIAL"; then
        echo "❌ 지정된 기기($TARGET_SERIAL)를 찾을 수 없습니다."
        exit 1
    fi
    echo "🚀 [$TARGET_SERIAL] 단일 미러링 창 실행..."
    scrcpy -s "$TARGET_SERIAL" \
        --window-title="Android ($TARGET_SERIAL)" \
        --max-size=960 \
        --stay-awake \
        --no-audio &
    echo "✔ 미러링이 실행되었습니다. (PID: $!)"
    exit 0
fi

# 0. 기존 실행 중인 scrcpy 창 전면 정리
killall scrcpy 2>/dev/null || true
sleep 0.5

# 3. 폰팜 그리드 모드 (all 또는 farm)
# 창 그리드 배치 설정 (가로 7개 창 배치)
COLS=7
WIN_WIDTH=260
WIN_HEIGHT=500
START_X=10
START_Y=30
SPACING_X=270
SPACING_Y=520

CURRENT_INDEX=0

if [ "$MAX_LIMIT" -gt 0 ]; then
    echo "🚀 [가로 7열 그리드 배치 모드] 대상 기기 중 상위 ${MAX_LIMIT}대만 화면에 타일링하여 실행합니다..."
else
    echo "🚀 [가로 7열 그리드 배치 모드] 대상 기기들을 화면에 타일링하여 실행합니다..."
fi

for SERIAL in "${DEVICES[@]}"; do
    if [ "$TARGET_MODE" == "farm" ] && [ "$SERIAL" == "$REAL_PHONE_SERIAL" ]; then
        continue
    fi

    # 개수 제한(MAX_LIMIT) 도달 시 종료
    if [ "$MAX_LIMIT" -gt 0 ] && [ "$CURRENT_INDEX" -ge "$MAX_LIMIT" ]; then
        break
    fi

    # 기기 모델명 추출
    MODEL=$(adb -s "$SERIAL" shell getprop ro.product.model 2>/dev/null | tr -d '\r')
    if [ -z "$MODEL" ]; then
        MODEL="Android"
    fi

    # 라벨 구분 (실기기 vs 폰팜)
    if [ "$SERIAL" == "$REAL_PHONE_SERIAL" ]; then
        TITLE="★ [순정 실기기] $MODEL ($SERIAL)"
    else
        TITLE="[폰팜 #$((CURRENT_INDEX + 1))] $MODEL ($SERIAL)"
    fi

    # 그리드 좌표 계산
    COL=$((CURRENT_INDEX % COLS))
    ROW=$((CURRENT_INDEX / COLS))
    POS_X=$((START_X + COL * SPACING_X))
    POS_Y=$((START_Y + ROW * SPACING_Y))

    echo "  • [$((CURRENT_INDEX + 1))/$TOTAL_COUNT] $TITLE -> ($POS_X, $POS_Y)"

    # 기기 내부 세로 모드(Portrait, 0도) 네이티브 하드 락 주입
    adb -s "$SERIAL" shell cmd window user-rotation lock 0 2>/dev/null || true
    adb -s "$SERIAL" shell cmd window fixed-to-user-rotation enabled 2>/dev/null || true
    adb -s "$SERIAL" shell settings put system accelerometer_rotation 0 2>/dev/null || true
    adb -s "$SERIAL" shell settings put system user_rotation 0 2>/dev/null || true

    scrcpy -s "$SERIAL" \
        --window-title="$TITLE" \
        --window-x="$POS_X" \
        --window-y="$POS_Y" \
        --window-width="$WIN_WIDTH" \
        --window-height="$WIN_HEIGHT" \
        --capture-orientation=@0 \
        --max-size=480 \
        --max-fps=30 \
        --stay-awake \
        --no-audio \
        >/dev/null 2>&1 &

    CURRENT_INDEX=$((CURRENT_INDEX + 1))
    sleep 0.15
done

echo ""
echo "================================================================================"
echo "🎉 총 ${CURRENT_INDEX}대 기기의 scrcpy 미러링 창이 성공적으로 실행되었습니다!"
echo "💡 모든 미러링 창을 닫으려면 'killall scrcpy'를 실행하세요."
echo "================================================================================"
