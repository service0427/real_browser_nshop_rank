"""
services/phone_farm/device_manager.py
안드로이드 실기기(USB & Wi-Fi/LAN) 폰팜 자동 검색, 헬스체크, 임대 및 CDP 포트 바인딩 관리자
"""

import os
import sys
import time
import subprocess
import asyncio
import urllib.request
import json
from typing import List, Dict, Any, Optional
from core.logger import get_logger

logger = get_logger("phone_farm.device_manager")


def get_excluded_devices() -> set:
    """제외/스킵할 기기 시리얼 목록 로드 (환경변수 EXCLUDED_DEVICES 또는 config/excluded_devices.txt)"""
    excluded = set(filter(None, [s.strip() for s in os.getenv("EXCLUDED_DEVICES", "").split(",")]))
    cfg_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config")
    cfg_path = os.path.join(cfg_dir, "excluded_devices.txt")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.split("#")[0].strip()
                    if line:
                        excluded.add(line)
        except Exception:
            pass
    return excluded


class PhoneDevice:
    """단일 폰팜 디바이스 인스턴스"""

    def __init__(self, serial: str, model: str = "", is_wifi: bool = False):
        self.serial = serial
        self.model = model
        self.is_wifi = is_wifi
        self.cdp_port: Optional[int] = None
        self.in_use: bool = False
        self.last_used_time: float = 0.0
        self.last_status: str = "IDLE"

    def __repr__(self):
        return f"<PhoneDevice {self.serial} ({self.model}) Port:{self.cdp_port} in_use:{self.in_use}>"


class PhoneDeviceManager:
    """폰팜 기기 풀 및 포트 포워딩 매니저"""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(PhoneDeviceManager, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, base_cdp_port: int = 9300):
        if self._initialized:
            return
        self.base_cdp_port = base_cdp_port
        self.devices: Dict[str, PhoneDevice] = {}
        self.lock = asyncio.Lock()
        self._initialized = True
        self._last_wifi_fail: Dict[str, float] = {}
        self.scan_devices()

    def scan_devices(self) -> List[Dict[str, Any]]:
        """ADB를 통해 현재 연결된 모든 실기기(USB/TCP) 스캔 (제외 목록 자동 필터링)"""
        try:
            res = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=5)
            lines = res.stdout.strip().splitlines()
            current_serials = set()
            excluded_serials = get_excluded_devices()

            for line in lines[1:]:
                line = line.strip()
                if not line or "offline" in line or "unauthorized" in line:
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    if serial in excluded_serials:
                        logger.debug(f"폰팜 기기 제외(블랙리스트): {serial}")
                        continue
                    current_serials.add(serial)
                    model = ""
                    for p in parts[2:]:
                        if p.startswith("model:"):
                            model = p.split(":", 1)[1]

                    if serial not in self.devices:
                        is_wifi = ":" in serial
                        self.devices[serial] = PhoneDevice(serial=serial, model=model, is_wifi=is_wifi)
                        logger.info(f"📱 [폰팜 새 기기 등록] {serial} ({model or 'Android'})")

            # 연결 해제된 기기 정리
            disconnected = [s for s in self.devices if s not in current_serials]
            for s in disconnected:
                logger.warning(f"⚠️ [폰팜 기기 연결 해제] {s}")
                del self.devices[s]

        except Exception as e:
            logger.error(f"기기 스캔 중 오류 발생: {e}")

        return [{"serial": d.serial, "model": d.model, "in_use": d.in_use, "port": d.cdp_port} for d in self.devices.values()]

    def ensure_device_wifi(self, serial: str) -> bool:
        """기기의 Wi-Fi 및 인터넷 연결 상태 빠른 확인 (1초 타임아웃). 미연결 시 빠른 스킵 (5분 쿨다운)"""
        # 최근 5분 이내에 이미 실패한 기기는 즉시 스킵
        if time.time() - self._last_wifi_fail.get(serial, 0) < 300:
            return False

        # 1초 핑 확인
        p_res = subprocess.run(["adb", "-s", serial, "shell", "ping", "-c", "1", "-W", "1", "8.8.8.8"], capture_output=True)
        if p_res.returncode == 0:
            return True

        # svc wifi enable 한 번 시도 및 tech_mik Wi-Fi 자동 보장
        subprocess.run(["adb", "-s", serial, "shell", "svc", "wifi", "enable"], capture_output=True)
        subprocess.run(["adb", "-s", serial, "shell", "cmd", "wifi", "add-suggestion", "tech_mik", "wpa2", "13241324"], capture_output=True)
        time.sleep(1.5)
        p_res = subprocess.run(["adb", "-s", serial, "shell", "ping", "-c", "1", "-W", "1", "8.8.8.8"], capture_output=True)
        if p_res.returncode == 0:
            return True

        logger.warning(f"⚠️ [폰팜 {serial}] 인터넷 끊김 감지 -> 이번 할당 스킵 (5분 쿨다운)")
        self._last_wifi_fail[serial] = time.time()
        return False

    async def acquire_device(self, worker_id: int = 1, wait_timeout: float = 30.0) -> Optional[PhoneDevice]:
        """사용 가능한 폰 1대 임대 및 CDP 포트 자동 바인딩 (유휴 폰 대기 지원)"""
        start_wait = time.time()
        device = None
        while time.time() - start_wait < wait_timeout:
            async with self.lock:
                self.scan_devices()
                idle_devices = [d for d in self.devices.values() if not d.in_use]
                excluded_serials = get_excluded_devices()
                for d in idle_devices:
                    if d.serial in excluded_serials:
                        continue
                    if self.ensure_device_wifi(d.serial):
                        device = d
                        device.in_use = True
                        device.cdp_port = self.base_cdp_port + worker_id
                        break
                    else:
                        logger.warning(f"⚠️ [폰팜 {d.serial}] Wi-Fi/인터넷 미연결로 이번 할당 스킵")
                if device:
                    break
            await asyncio.sleep(1.0)

        if not device:
            logger.warning(f"⚠️ [폰팜] 사용 가능한 유휴 폰이 없습니다 (대기 초과 {wait_timeout}초).")
            return None

        # 0. 화면 자동 켜기 + 화면 잠금 해제 + 상시 켜짐 유지 + 세로 모드(Portrait) 강제 고정
        subprocess.run(["adb", "-s", device.serial, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True)
        subprocess.run(["adb", "-s", device.serial, "shell", "wm", "dismiss-keyguard"], capture_output=True)
        subprocess.run(["adb", "-s", device.serial, "shell", "svc", "power", "stayon", "true"], capture_output=True)
        subprocess.run(["adb", "-s", device.serial, "shell", "settings", "put", "system", "screen_off_timeout", "2147483647"], capture_output=True)
        subprocess.run(["adb", "-s", device.serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0"], capture_output=True)
        subprocess.run(["adb", "-s", device.serial, "shell", "settings", "put", "system", "user_rotation", "0"], capture_output=True)

        # 0-1. 시스템 및 크롬 한글 로케일 강제 설정 (영문 UI / 네이버 로그인 유도 방지)
        subprocess.run(["adb", "-s", device.serial, "shell", "settings", "put", "system", "system_locales", "ko-KR,en-US"], capture_output=True)
        subprocess.run(["adb", "-s", device.serial, "shell", "cmd", "locale", "set-app-locales", "com.android.chrome", "--locales", "ko-KR,ko"], capture_output=True)

        # 1. 폰에 크롬 브라우저 전면 포그라운드 활성화 (새 탭 생성 없이 포그라운드 전환)
        subprocess.run(["adb", "-s", device.serial, "shell", "am", "start", "-n", "com.android.chrome/com.google.android.apps.chrome.Main"], capture_output=True)
        await asyncio.sleep(0.5)

        # 2. Chrome DevTools 원격 디버깅 소켓 포트 포워딩
        self._bind_cdp_port(device)

        # 3. 백그라운드 누적 탭 정리 (기기 메모리 고갈 방지: 최대 1~2개 활성 탭 유지 및 로그인창 탭 자동 종료)
        self.cleanup_device_tabs(device.cdp_port)

        logger.info(f"✔ [폰팜 기기 할당 완료] {device.serial} -> CDP Port: {device.cdp_port}")
        return device

    def _bind_cdp_port(self, device: PhoneDevice):
        """Chrome DevTools 원격 디버깅 소켓 포트 포워딩 바인딩"""
        sock_res = subprocess.run(["adb", "-s", device.serial, "shell", "grep", "-a", "chrome_devtools_remote", "/proc/net/unix"], capture_output=True, text=True)
        sock_name = "chrome_devtools_remote"
        for l in sock_res.stdout.splitlines():
            if "chrome_devtools_remote" in l:
                sock_name = l.split()[-1].replace("@", "").strip()
                break
        subprocess.run(["adb", "-s", device.serial, "forward", f"tcp:{device.cdp_port}", f"localabstract:{sock_name}"], capture_output=True)

    def cleanup_device_tabs(self, port: Optional[int]):
        """백그라운드 누적 탭 정리 (최대 1~2개 활성 탭 유지 및 로그인창 탭 자동 종료)"""
        if not port:
            return
        try:
            tabs_data = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1.0).read())
            # 1. 로그인 페이지(nid.naver.com / nidlogin) 탭 자동 정리
            for t in tabs_data:
                url = t.get("url", "")
                if ("nid.naver.com" in url or "nidlogin" in url) and t.get("id"):
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{port}/json/close/{t.get('id')}")
                        urllib.request.urlopen(req, timeout=0.5)
                    except Exception:
                        pass
            # 2. 초과 탭 정리 (최대 1~2개 활성 탭 유지)
            if len(tabs_data) > 3:
                keep_id = tabs_data[0].get("id")
                for t in tabs_data[1:]:
                    if t.get("type") == "page":
                        try:
                            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/close/{t.get('id')}")
                            urllib.request.urlopen(req, timeout=0.5)
                        except Exception:
                            pass
        except Exception:
            pass

    def restart_device_chrome(self, device: PhoneDevice):
        """크롬 렌더러 크래시('앗, 이런!') 또는 CDP 소켓 먹통 시 크롬 프로세스 강제 재기동"""
        logger.warning(f"🔄 [폰팜 {device.serial}] 크롬 크래시/먹통 감지 -> 프로세스 강제 재기동 수행")
        subprocess.run(["adb", "-s", device.serial, "shell", "am", "force-stop", "com.android.chrome"], capture_output=True)
        time.sleep(1.0)
        subprocess.run(["adb", "-s", device.serial, "shell", "am", "start", "-n", "com.android.chrome/com.google.android.apps.chrome.Main"], capture_output=True)
        time.sleep(1.5)
        self._bind_cdp_port(device)
        self.cleanup_device_tabs(device.cdp_port)

    async def release_device(self, device: PhoneDevice):
        """작업 완료 후 폰 반납 및 포트 정리"""
        async with self.lock:
            if device.serial in self.devices:
                self.devices[device.serial].in_use = False
                if device.cdp_port:
                    subprocess.run(["adb", "-s", device.serial, "forward", "--remove", f"tcp:{device.cdp_port}"], capture_output=True)
                logger.info(f"🔓 [폰팜 기기 반납 완료] {device.serial}")


phone_device_mgr = PhoneDeviceManager()
