#!/usr/bin/env bash
# ==============================================================================
# 🚀 TechB NShop Rank Crawler - 우분투 신규 서버 자동 원클릭 설치 스크립트
# ==============================================================================
# 사용법:
#   chmod +x setup.sh
#   ./setup.sh
# ==============================================================================

set -e

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "\n${CYAN}================================================================================${NC}"
echo -e "${CYAN}🚀 [TechB NShop Rank] 우분투 GUI 서버 자동 환경 설정 및 의존성 설치 시작${NC}"
echo -e "${CYAN}================================================================================${NC}\n"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 1. 시스템 필수 패키지 및 한글 폰트 설치
echo -e "${BLUE}📦 [1/6] 우분투 시스템 패키지 및 네이버 렌더링 한글 폰트 설치 중...${NC}"
sudo apt-get update -y
sudo apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    curl \
    wget \
    psmisc \
    jq \
    unzip \
    libnss3 \
    libgbm1 \
    libasound2t64 \
    fonts-nanum \
    fonts-nanum-coding \
    fonts-nanum-extra \
    fonts-noto-cjk

# 2. 구글 공식 Chrome 브라우저 설치 확인 및 설치
echo -e "\n${BLUE}🌐 [2/6] Google Chrome 브라우저 설치 확인 중...${NC}"
if ! command -v google-chrome &> /dev/null; then
    echo -e "${YELLOW}  -> Google Chrome이 설치되어 있지 않습니다. 공식 deb 다운로드 및 설치를 진행합니다.${NC}"
    TMP_DEB="/tmp/google-chrome-stable_current_amd64.deb"
    wget -q -O "$TMP_DEB" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    sudo apt-get install -y "$TMP_DEB"
    rm -f "$TMP_DEB"
    echo -e "${GREEN}  ✔ Google Chrome 설치 완료: $(google-chrome --version)${NC}"
else
    echo -e "${GREEN}  ✔ Google Chrome이 이미 설치되어 있습니다: $(google-chrome --version)${NC}"
fi

# 3. Python 가상환경(venv) 생성 및 활성화
echo -e "\n${BLUE}🐍 [3/6] Python 가상환경(venv) 생성 및 패키지 설치 중...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}  ✔ Python venv 가상환경 생성 완료${NC}"
fi

# pip 업그레이드 및 requirements 설치
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
echo -e "${GREEN}  ✔ requirements.txt 의존성 패키지 설치 완료${NC}"

# 4. Playwright 브라우저 의존성 설정
echo -e "\n${BLUE}🎭 [4/6] Playwright 브라우저 의존성 확인 중...${NC}"
./venv/bin/playwright install chromium 2>/dev/null || true
echo -e "${GREEN}  ✔ Playwright 브라우저 엔진 준비 완료${NC}"

# 5. 필수 런타임 캐시 및 프로필 디렉토리 생성
echo -e "\n${BLUE}📁 [5/6] 런타임 캐시 및 프로필 풀 디렉토리 생성 중...${NC}"
mkdir -p services/runtime/{profiles,master_profile,keyword_cache,browser_cache,logs}

# 6. 실행 권한 부여
echo -e "\n${BLUE}🔑 [6/6] 실행 스크립트 권한 설정 중...${NC}"
chmod +x setup.sh 2>/dev/null || true
chmod +x start_worker.sh 2>/dev/null || true
chmod +x main.py 2>/dev/null || true

echo -e "\n${GREEN}================================================================================${NC}"
echo -e "${GREEN}🎉 [설치 완료] 모든 환경 설정과 의존성 설치가 성공적으로 끝났습니다!${NC}"
echo -e "${GREEN}================================================================================${NC}"
echo -e "${CYAN}💡 워커 실행 방법:${NC}"
echo -e "   • 4쓰레드 GUI 실행: ${YELLOW}./start_worker.sh 4${NC}"
echo -e "   • 8쓰레드 GUI 실행: ${YELLOW}./start_worker.sh 8${NC}"
echo -e "${GREEN}================================================================================${NC}\n"
