"""
services/ip_toggle.py
MikroTik 라우터(Node-090) 및 상위 장비 IP 토글 매니저
- 최소 10분(600초) 쿨다운 제어
- WAN(ether1) MAC 회전 및 DHCP 갱신
- 신규 공인 IP 할당 및 인터넷 연결 완료까지 타임아웃 기반 스마트 헬스체크
"""

import os
import sys
import time
import json
import random
import socket
import urllib.request
from typing import Dict, Any, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.logger import get_logger

logger = get_logger("infra.ip_toggle")

STATE_FILE = os.path.join(BASE_DIR, "services", "runtime", "ip_toggle_state.json")
ROUTER_IP = os.getenv("ROUTER_IP", "192.168.88.1")
ROUTER_PORT = int(os.getenv("ROUTER_PORT", 8728))
ROUTER_USER = os.getenv("ROUTER_USER", "techhh")
ROUTER_PASS = os.getenv("ROUTER_PASS", "Tech1324!05hh")
MIN_COOLDOWN_SECONDS = int(os.getenv("IP_TOGGLE_COOLDOWN", 600))  # 최소 10분 간격


class RouterOSAPIClient:
    """MikroTik RouterOS API 기본 통신 클라이언트 (Port 8728)"""

    def __init__(self, host: str = ROUTER_IP, port: int = ROUTER_PORT, user: str = ROUTER_USER, password: str = ROUTER_PASS):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10.0)
            self.sock.connect((self.host, self.port))
            # Login
            res = self.query(['/login', f'=name={self.user}', f'=password={self.password}'])
            for sentence in res:
                if '!done' in sentence:
                    return True
                if '!trap' in sentence:
                    logger.error(f"[RouterOS] 로그인 실패: {sentence}")
                    return False
            return False
        except Exception as e:
            logger.error(f"[RouterOS] 연결 오류 ({self.host}:{self.port}): {e}")
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def _send_word(self, word: str):
        b = word.encode('utf-8')
        l = len(b)
        if l < 0x80:
            self.sock.send(bytes([l]))
        elif l < 0x4000:
            l |= 0x8000
            self.sock.send(bytes([(l >> 8) & 0xFF, l & 0xFF]))
        else:
            l |= 0xC00000
            self.sock.send(bytes([(l >> 16) & 0xFF, (l >> 8) & 0xFF, l & 0xFF]))
        self.sock.send(b)

    def _send_sentence(self, words: list):
        for w in words:
            self._send_word(w)
        self.sock.send(b'\x00')

    def _read_word(self) -> Optional[str]:
        b = self.sock.recv(1)
        if not b:
            return None
        l = b[0]
        if l & 0x80:
            if (l & 0xC0) == 0x80:
                b2 = self.sock.recv(1)
                l = ((l & 0x3F) << 8) | b2[0]
            elif (l & 0xE0) == 0xC0:
                b2 = self.sock.recv(2)
                l = ((l & 0x1F) << 16) | (b2[0] << 8) | b2[1]
        data = b''
        while len(data) < l:
            chunk = self.sock.recv(l - len(data))
            if not chunk:
                break
            data += chunk
        return data.decode('utf-8', errors='ignore')

    def query(self, words: list) -> list:
        if not self.sock:
            return []
        self._send_sentence(words)
        sentences = []
        while True:
            res = []
            while True:
                w = self._read_word()
                if w is None or w == '':
                    break
                res.append(w)
            if not res:
                break
            sentences.append(res)
            if '!done' in res or '!trap' in res or '!fatal' in res:
                break
        return sentences


class IPToggleManager:
    """
    IP 토글 매니저
    - 10분 쿨다운 보장
    - WAN MAC 랜덤 생성 및 갱신
    - DHCP Release / Renew
    - 새 IP 할당 및 인터넷 연결 복구 완료까지 폴링 타임아웃 헬스체크
    """

    @staticmethod
    def _generate_random_mac(prefix: str = "F4:1E:57") -> str:
        """MikroTik OUI 기반 유효 랜덤 MAC 주소 생성"""
        p_bytes = prefix.split(":")
        suffix = [f"{random.randint(0x00, 0xFE):02X}" for _ in range(6 - len(p_bytes))]
        return ":".join(p_bytes + suffix)

    @classmethod
    def get_public_ip_external(cls, timeout: float = 3.0) -> Optional[str]:
        """외부 에코 서비스를 통한 현재 공인 IP 확인"""
        services = [
            "http://api.ipify.org",
            "http://ifconfig.me/ip",
            "http://icanhazip.com"
        ]
        for s in services:
            try:
                req = urllib.request.Request(s, headers={"User-Agent": "curl/7.68.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        text = resp.read().decode('utf-8', errors='ignore').strip()
                        if len(text) <= 45 and "." in text:
                            return text
            except Exception:
                continue
        return None

    @classmethod
    def get_wan_info(cls) -> Dict[str, Any]:
        """MikroTik ether1 인터페이스의 IP 및 MAC 주소 조회"""
        client = RouterOSAPIClient()
        if not client.connect():
            return {"connected": False, "ip": None, "mac": None}

        info = {"connected": True, "ip": None, "mac": None, "status": "unknown"}
        try:
            # 1. MAC 주소
            eth_res = client.query(['/interface/ethernet/print', '?name=ether1'])
            for st in eth_res:
                for item in st:
                    if item.startswith("=mac-address="):
                        info["mac"] = item.split("=")[2]

            # 2. DHCP IP 주소
            dhcp_res = client.query(['/ip/dhcp-client/print', '?interface=ether1'])
            for st in dhcp_res:
                for item in st:
                    if item.startswith("=address="):
                        info["ip"] = item.split("=")[2].split("/")[0]
                    elif item.startswith("=status="):
                        info["status"] = item.split("=")[2]
        finally:
            client.close()

        return info

    @classmethod
    def load_state(cls) -> Dict[str, Any]:
        """상태 파일 로드"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_toggle_time": 0, "last_old_ip": None, "last_new_ip": None, "toggle_count": 0}

    @classmethod
    def save_state(cls, state: Dict[str, Any]):
        """상태 파일 저장"""
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"토글 상태 저장 실패: {e}")

    @classmethod
    def check_cooldown(cls) -> Tuple[bool, int]:
        """
        10분 쿨다운 검사
        반환값: (is_ready, remaining_seconds)
        """
        state = cls.load_state()
        last_time = state.get("last_toggle_time", 0)
        elapsed = time.time() - last_time
        remaining = int(MIN_COOLDOWN_SECONDS - elapsed)
        if remaining > 0:
            return False, remaining
        return True, 0

    @classmethod
    def toggle_ip(cls, force: bool = False, timeout_sec: int = 90) -> Dict[str, Any]:
        """
        IP 토글 실행 (WAN MAC 회전 + DHCP 갱신 + 복구 헬스체크)
        """
        is_ready, remaining_sec = cls.check_cooldown()
        if not is_ready and not force:
            logger.warning(f"⏳ [IP 토글 대기] 10분 쿨다운 미도달 (남은 시간: {remaining_sec // 60}분 {remaining_sec % 60}초)")
            return {
                "success": False,
                "reason": "COOLDOWN_ACTIVE",
                "remaining_seconds": remaining_sec,
                "message": f"최소 10분 간격 제어: {remaining_sec}초 후 재시도 가능"
            }

        start_time = time.time()
        logger.info("=" * 80)
        logger.info("🔄 [IP 토글 시작] 상위 장비 및 MikroTik WAN(ether1) IP 갱신 절차 돌입")
        logger.info("=" * 80)

        # 1. 이전 IP 및 MAC 확인
        old_info = cls.get_wan_info()
        old_ip = old_info.get("ip") or cls.get_public_ip_external() or "UNKNOWN"
        old_mac = old_info.get("mac") or "UNKNOWN"
        logger.info(f"📌 [현재 상태] 기존 IP: {old_ip} | 기존 MAC: {old_mac}")

        # 2. 새로운 랜덤 MAC 생성
        new_mac = cls._generate_random_mac(prefix="F4:1E:57")
        logger.info(f"🎲 [신규 MAC 생성] 새 ether1 MAC: {new_mac}")

        # 3. RouterOS API 연결 및 설정 적용
        client = RouterOSAPIClient()
        if not client.connect():
            logger.error("❌ [IP 토글 실패] RouterOS API (192.168.88.1) 연결 불가")
            return {"success": False, "reason": "ROUTER_CONNECT_FAILED"}

        try:
            # ether1 MAC 변경
            logger.info("⚙️ [1/3] ether1 MAC 주소 변경 적용 중...")
            client.query(['/interface/ethernet/set', '=.id=*1', f'=mac-address={new_mac}'])

            # DHCP Client Release
            logger.info("⚙️ [2/3] DHCP Client Release 수행 중...")
            client.query(['/ip/dhcp-client/release', '=.id=*1'])

            time.sleep(1.0)

            # DHCP Client Renew
            logger.info("⚙️ [3/3] DHCP Client Renew 수행 중...")
            client.query(['/ip/dhcp-client/renew', '=.id=*1'])

        except Exception as e:
            logger.error(f"❌ [RouterOS 명령어 실행 중 예외]: {e}")
        finally:
            client.close()

        # 4. 새 IP 할당 및 인터넷 연결 복구 완료까지 폴링 대기 (타임아웃 헬스체크)
        logger.info(f"⏳ [연결 대기] 새 공인 IP 할당 및 인터넷 복구 대기 (최대 {timeout_sec}초)...")
        new_ip = None
        is_connected = False
        poll_interval = 2.0
        max_attempts = int(timeout_sec / poll_interval)

        for attempt in range(1, max_attempts + 1):
            time.sleep(poll_interval)
            wan_info = cls.get_wan_info()
            curr_ip = wan_info.get("ip")
            status = wan_info.get("status")

            if curr_ip and status == "bound":
                # 외부 인터넷 연결 확인
                ext_ip = cls.get_public_ip_external(timeout=2.0)
                if ext_ip or curr_ip != old_ip:
                    new_ip = curr_ip or ext_ip
                    is_connected = True
                    elapsed = round(time.time() - start_time, 2)
                    logger.info(f"🟢 [연결 성공] 새 IP 할당 완료! (경과: {elapsed}초, 시도: {attempt}회)")
                    logger.info(f"   • 이전 IP : {old_ip} ({old_mac})")
                    logger.info(f"   • 신규 IP : {new_ip} ({new_mac})")
                    break

            if attempt % 5 == 0:
                elapsed = round(time.time() - start_time, 1)
                logger.info(f"   ... IP 할당 대기 중 ({elapsed}s/{timeout_sec}s, 상태: {status}, IP: {curr_ip or '할당중'})...")

        total_elapsed = round(time.time() - start_time, 2)

        # 5. 결과 기록 및 상태 저장
        if is_connected:
            state = cls.load_state()
            state["last_toggle_time"] = int(time.time())
            state["last_old_ip"] = old_ip
            state["last_new_ip"] = new_ip
            state["toggle_count"] = state.get("toggle_count", 0) + 1
            cls.save_state(state)

            logger.info("=" * 80)
            logger.info(f"🎉 [IP 토글 성공] {old_ip} ➜ {new_ip} (소요 시간: {total_elapsed}초)")
            logger.info("=" * 80)
            return {
                "success": True,
                "old_ip": old_ip,
                "new_ip": new_ip,
                "new_mac": new_mac,
                "duration_sec": total_elapsed,
                "ip_changed": (old_ip != new_ip)
            }
        else:
            logger.error(f"❌ [IP 토글 타임아웃] {timeout_sec}초 내에 새 IP 할당 또는 인터넷 연결이 완료되지 않았습니다.")
            return {
                "success": False,
                "reason": "TIMEOUT_WAITING_IP",
                "old_ip": old_ip,
                "new_ip": None,
                "duration_sec": total_elapsed
            }


ip_toggle_mgr = IPToggleManager()
