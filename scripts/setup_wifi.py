#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/setup_wifi.py
안드로이드 실기기(루팅폰/일반폰) 수십대 동시 Wi-Fi 일괄 연결 및 자동 검증 스크립트
- USB로 연결된 모든 기기를 병렬(Multi-threading)로 동시 설정
- 1단계: root(su) API 직접 연결 시도
- 2단계: UI Automator 화면 제어 자동 연결 (Samsung OneUI/Android 표준 설정)
- 3단계: 할당된 IP 확인 및 Ping 테스트로 인터넷 실연결 검증
"""

import sys
import time
import argparse
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple


def run_adb(serial: str, args: List[str], timeout: float = 10.0) -> Tuple[int, str, str]:
    """ADB 명령어 안전 실행 헬퍼"""
    cmd = ["adb", "-s", serial] + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout, res.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def get_connected_devices() -> List[Dict[str, str]]:
    """현재 USB로 연결된 인가(device) 상태의 모든 기기 목록 반환"""
    try:
        res = subprocess.run(["adb", "devices", "-l"], capture_output=True, text=True, timeout=5)
        devices = []
        for line in res.stdout.strip().splitlines()[1:]:
            line = line.strip()
            if not line or "offline" in line or "unauthorized" in line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                model = ""
                for p in parts[2:]:
                    if p.startswith("model:"):
                        model = p.split(":", 1)[1]
                devices.append({"serial": serial, "model": model or "Android"})
        return devices
    except Exception as e:
        print(f"❌ 기기 목록 조회 실패: {e}")
        return []


def check_wifi_connection(serial: str, target_ssid: str) -> Tuple[bool, str, bool]:
    """
    기기의 현재 Wi-Fi 연결 상태 확인
    반환: (is_connected_to_target, ip_address, has_internet)
    """
    # 1. cmd wifi status 확인
    _, out, _ = run_adb(serial, ["shell", "cmd", "wifi", "status"], timeout=4.0)
    is_connected = False
    if f'"{target_ssid}"' in out or f"'{target_ssid}'" in out or target_ssid in out:
        if "COMPLETED" in out or "isUsable: true" in out or "connected to" in out.lower():
            is_connected = True

    # 2. wlan0 IP 확인
    ip_addr = "N/A"
    _, ip_out, _ = run_adb(serial, ["shell", "ip", "-4", "addr", "show", "wlan0"], timeout=3.0)
    for line in ip_out.splitlines():
        line = line.strip()
        if line.startswith("inet "):
            ip_addr = line.split()[1].split("/")[0]
            if ip_addr and ip_addr != "127.0.0.1":
                if not is_connected and target_ssid == "":
                    is_connected = True
            break

    # 3. 인터넷 핑 확인 (8.8.8.8)
    has_internet = False
    if ip_addr != "N/A":
        _, ping_out, _ = run_adb(serial, ["shell", "ping", "-c", "1", "-W", "2", "8.8.8.8"], timeout=4.0)
        has_internet = "bytes from" in ping_out

    return is_connected, ip_addr, has_internet


def connect_device_suggestion_method(serial: str, ssid: str, password: str) -> bool:
    """[Android 10+ 표준/OneUI] cmd wifi add-suggestion 및 구 네트워크 정리"""
    sec_type = "wpa2" if password else "open"
    # 1. 새 네트워크 suggestion 등록
    cmd_args = ["shell", "cmd", "wifi", "add-suggestion", ssid, sec_type]
    if password:
        cmd_args.append(password)
    run_adb(serial, cmd_args, timeout=5.0)

    # 2. 다른 기존 네트워크 forget 처리 (이전 공유기로의 회귀 차단)
    _, list_net, _ = run_adb(serial, ["shell", "cmd", "wifi", "list-networks"], timeout=5.0)
    for line in list_net.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            net_id = parts[0]
            cur_net_ssid = parts[1]
            if cur_net_ssid != ssid:
                run_adb(serial, ["shell", "cmd", "wifi", "forget-network", net_id], timeout=3.0)

    # 3. 스캔 트리거 및 연결 유도
    run_adb(serial, ["shell", "cmd", "wifi", "start-scan"], timeout=3.0)
    time.sleep(3.0)
    is_conn, _, _ = check_wifi_connection(serial, ssid)
    if not is_conn:
        # Wi-Fi 토글로 강제 재연결 유도
        run_adb(serial, ["shell", "svc", "wifi", "disable"], timeout=3.0)
        time.sleep(1.0)
        run_adb(serial, ["shell", "svc", "wifi", "enable"], timeout=3.0)
        time.sleep(4.0)
        is_conn, _, _ = check_wifi_connection(serial, ssid)
    return is_conn


def connect_device_root_method(serial: str, ssid: str, password: str) -> bool:
    """[루팅폰 전용] su 권한을 이용한 빠른 백그라운드 Wi-Fi 연결"""
    # 1. su 지원 여부 확인
    code, out, _ = run_adb(serial, ["shell", "which", "su"], timeout=3.0)
    if code != 0 or not out.strip():
        return False

    # 2. su를 통해 cmd wifi connect-network 호출
    sec_type = "wpa2" if password else "open"
    cmd_str = f"cmd wifi connect-network {ssid} {sec_type} {password}" if password else f"cmd wifi connect-network {ssid} open"
    run_adb(serial, ["shell", "su", "-c", cmd_str], timeout=5.0)

    # 3. suggestion 추가 및 shell 승인
    sugg_cmd = f"cmd wifi add-suggestion {ssid} {sec_type} {password} -s" if password else f"cmd wifi add-suggestion {ssid} open -s"
    run_adb(serial, ["shell", "su", "-c", sugg_cmd], timeout=5.0)
    run_adb(serial, ["shell", "su", "-c", "cmd wifi network-suggestions-set-user-approved com.android.shell yes"], timeout=3.0)

    # 4. 연결 대기
    time.sleep(3.0)
    is_conn, _, _ = check_wifi_connection(serial, ssid)
    return is_conn


def connect_device_ui_method(serial: str, ssid: str, password: str) -> bool:
    """[UI Automator] 설정 화면을 직접 제어하여 Wi-Fi 연결 (모든 기기 호환)"""
    # 0. 설정 앱 초기화 후 Wi-Fi 설정 진입
    run_adb(serial, ["shell", "am", "force-stop", "com.android.settings"], timeout=3.0)
    run_adb(serial, ["shell", "am", "start", "-a", "android.settings.WIFI_SETTINGS"], timeout=4.0)
    time.sleep(2.0)

    # Connections 화면인 경우 Wi-Fi 메뉴 진입 시도
    run_adb(serial, ["shell", "uiautomator", "dump", "/data/local/tmp/dump.xml"], timeout=6.0)
    _, xml_data, _ = run_adb(serial, ["shell", "cat", "/data/local/tmp/dump.xml"], timeout=4.0)

    try:
        root = ET.fromstring(xml_data)
        for node in root.iter("node"):
            if node.get("text") in ["Connections", "연결", "네트워크 및 인터넷"]:
                for sub in root.iter("node"):
                    if sub.get("text") in ["Wi-Fi", "와이파이"]:
                        b = sub.get("bounds", "")
                        pts = [int(p) for p in b.replace("][", ",").replace("[", "").replace("]", "").split(",")]
                        run_adb(serial, ["shell", "input", "tap", str((pts[0]+pts[2])//2), str((pts[1]+pts[3])//2)], timeout=3.0)
                        time.sleep(2.0)
                        break
                break
    except Exception:
        pass

    # Wi-Fi 스캔 대기 (SSID 탐색)
    target_pos = None
    for attempt in range(2):
        run_adb(serial, ["shell", "uiautomator", "dump", "/data/local/tmp/dump.xml"], timeout=6.0)
        _, xml_data, _ = run_adb(serial, ["shell", "cat", "/data/local/tmp/dump.xml"], timeout=4.0)
        try:
            root = ET.fromstring(xml_data)
            for node in root.iter("node"):
                text_val = node.get("text", "")
                if text_val == ssid:
                    b = node.get("bounds", "")
                    pts = [int(p) for p in b.replace("][", ",").replace("[", "").replace("]", "").split(",")]
                    target_pos = ((pts[0] + pts[2]) // 2, (pts[1] + pts[3]) // 2)
                    break
        except Exception:
            pass

        if target_pos:
            break
        # 못 찾으면 살짝 스크롤 후 재시도
        if attempt == 0:
            run_adb(serial, ["shell", "input", "swipe", "500", "1200", "500", "700", "300"], timeout=3.0)
            time.sleep(1.5)

    if not target_pos:
        return False

    # 1. SSID 터치
    run_adb(serial, ["shell", "input", "tap", str(target_pos[0]), str(target_pos[1])], timeout=3.0)
    time.sleep(1.5)

    # 2. 비밀번호 입력
    if password:
        # 안전한 텍스트 입력을 위해 공백 및 특수문자 이스케이프 처리
        safe_pw = password.replace(" ", "%s").replace("&", r"\&").replace("<", r"\<").replace(">", r"\>")
        run_adb(serial, ["shell", "input", "text", safe_pw], timeout=4.0)
        time.sleep(0.5)

    # 3. '연결' 버튼 찾기 또는 엔터(66) 전송
    connected_clicked = False
    run_adb(serial, ["shell", "uiautomator", "dump", "/data/local/tmp/pw_dump.xml"], timeout=6.0)
    _, pw_xml, _ = run_adb(serial, ["shell", "cat", "/data/local/tmp/pw_dump.xml"], timeout=4.0)
    try:
        root = ET.fromstring(pw_xml)
        for node in root.iter("node"):
            if node.get("text") in ["연결", "Connect", "저장", "Save"]:
                b = node.get("bounds", "")
                pts = [int(p) for p in b.replace("][", ",").replace("[", "").replace("]", "").split(",")]
                run_adb(serial, ["shell", "input", "tap", str((pts[0]+pts[2])//2), str((pts[1]+pts[3])//2)], timeout=3.0)
                connected_clicked = True
                break
    except Exception:
        pass

    if not connected_clicked:
        run_adb(serial, ["shell", "input", "keyevent", "66"], timeout=3.0)

    # 4. 연결 대기 및 홈 이동
    time.sleep(4.0)
    run_adb(serial, ["shell", "input", "keyevent", "3"], timeout=2.0)
    return True


def setup_single_device(device: Dict[str, str], ssid: str, password: str, force: bool = False) -> Dict[str, Any]:
    """단일 기기 Wi-Fi 연결 및 상태 진단 작업"""
    serial = device["serial"]
    model = device["model"]

    # 0. 기기 깨우기 & 잠금해제 & Wi-Fi 활성화
    run_adb(serial, ["shell", "input", "keyevent", "224"], timeout=3.0)
    run_adb(serial, ["shell", "wm", "dismiss-keyguard"], timeout=3.0)
    run_adb(serial, ["shell", "svc", "power", "stayon", "true"], timeout=2.0)
    run_adb(serial, ["shell", "svc", "wifi", "enable"], timeout=3.0)

    # 1. 현재 이미 타겟 Wi-Fi에 연결되어 인터넷이 되는지 확인
    if not force:
        is_conn, ip, ok = check_wifi_connection(serial, ssid)
        if is_conn and ok:
            return {
                "serial": serial,
                "model": model,
                "status": "ALREADY_CONNECTED",
                "ip": ip,
                "internet": True,
                "msg": "이미 정상 연결됨"
            }

    # 2. [1차 시도] cmd wifi suggestion & forget (순정/루팅 공통 초고속 백그라운드)
    method_used = "CMD_WIFI_SUGGESTION"
    success = connect_device_suggestion_method(serial, ssid, password)

    # 3. [2차 시도] 실패 시 루팅폰 root(su) 직결 시도
    if not success:
        method_used = "ROOT_SU"
        success = connect_device_root_method(serial, ssid, password)

    # 4. [3차 시도] 실패 시 UI Automator 자동 화면 제어 시도
    if not success:
        method_used = "UI_AUTOMATOR"
        connect_device_ui_method(serial, ssid, password)

    # 5. 최종 연결 검증
    time.sleep(3.0)
    is_conn, ip, ok = check_wifi_connection(serial, ssid)

    status_str = "SUCCESS" if (is_conn or ok) else "FAILED"
    return {
        "serial": serial,
        "model": model,
        "status": status_str,
        "method": method_used,
        "ip": ip,
        "internet": ok,
        "msg": "연결 성공" if (is_conn or ok) else "SSID 미발견 또는 연결 실패"
    }


def main():
    parser = argparse.ArgumentParser(description="TechB 안드로이드 실기기 Wi-Fi 일괄 설정 도구")
    parser.add_argument("--ssid", "-s", default="tech_mik", help="연결할 Wi-Fi SSID (기본: tech_mik)")
    parser.add_argument("--password", "-p", default="13241324", help="Wi-Fi 비밀번호 (기본: 13241324)")
    parser.add_argument("--force", "-f", action="store_true", help="이미 연결된 기기도 강제 재연결")
    parser.add_argument("--concurrency", "-c", type=int, default=10, help="동시 설정 쓰레드 수 (기본: 10)")
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print(f"📶 [TechB WiFi Manager] 안드로이드 실기기 Wi-Fi 일괄 설정 시작")
    print(f"• 대상 SSID     : {args.ssid}")
    print(f"• 비밀번호 설정 : {'[설정됨]' if args.password else '[비밀번호 없음 (Open)]'}")
    print(f"• 강제 재연결   : {'예 (Force)' if args.force else '아니오 (이미 연결된 기기는 Skip)'}")
    print("=" * 80)

    devices = get_connected_devices()
    if not devices:
        print("❌ [오류] 연결된 안드로이드 기기가 없습니다. USB 케이블 연결 상태를 확인하세요.")
        sys.exit(1)

    print(f"📱 총 {len(devices)}대의 기기가 감지되었습니다. 병렬 Wi-Fi 설정을 시작합니다...\n")

    results = []
    concurrency = min(len(devices), max(1, args.concurrency))

    start_time = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_device = {
            executor.submit(setup_single_device, dev, args.ssid, args.password, args.force): dev
            for dev in devices
        }

        for future in as_completed(future_to_device):
            dev = future_to_device[future]
            try:
                res = future.result()
                results.append(res)
                icon = "🟢" if (res["status"] in ["SUCCESS", "ALREADY_CONNECTED"] and res["internet"]) else ("🟡" if res["status"] in ["SUCCESS", "ALREADY_CONNECTED"] else "🔴")
                print(f"  {icon} [{res['serial']}] {res['model']} -> {res['msg']} (IP: {res['ip']}, 인터넷: {'OK' if res['internet'] else 'NO'})")
            except Exception as e:
                print(f"  🔴 [{dev['serial']}] 예외 발생: {e}")
                results.append({"serial": dev["serial"], "model": dev["model"], "status": "ERROR", "ip": "N/A", "internet": False, "msg": str(e)})

    elapsed = round(time.time() - start_time, 2)

    # 종합 결과 요약 출력
    success_count = sum(1 for r in results if r["status"] in ["SUCCESS", "ALREADY_CONNECTED"] and r["internet"])
    partial_count = sum(1 for r in results if r["status"] in ["SUCCESS", "ALREADY_CONNECTED"] and not r["internet"])
    fail_count = sum(1 for r in results if r["status"] not in ["SUCCESS", "ALREADY_CONNECTED"])

    print("\n" + "=" * 80)
    print("📋 [Wi-Fi 일괄 설정 최종 결과]")
    print("=" * 80)
    print(f"{'시리얼':<16} | {'기기 모델':<14} | {'상태':<12} | {'할당 IP':<16} | {'인터넷 Ping'}")
    print("-" * 80)
    for r in results:
        status_display = "🟢 연결성공" if r["status"] in ["SUCCESS", "ALREADY_CONNECTED"] and r["internet"] else ("🟡 IP할당완료" if r["status"] in ["SUCCESS", "ALREADY_CONNECTED"] else "🔴 실패")
        internet_display = "정상 (Ping OK)" if r["internet"] else "확인 불가"
        print(f"{r['serial']:<16} | {r['model']:<14} | {status_display:<12} | {r['ip']:<16} | {internet_display}")
    print("-" * 80)
    print(f"🎉 총 {len(devices)}대 중 {success_count}대 인터넷 연결 완료 (소요시간: {elapsed}초) | 부분연결: {partial_count}대 | 실패: {fail_count}대")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
