#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/infra/toggle_ip.py
MikroTik 라우터 및 상위 장비 공인 IP 토글 독립 실행 스크립트
- git pull 등 원격 업데이트 시 덮어쓰기 방지를 위한 격리 스크립트

사용법:
  python3 scripts/infra/toggle_ip.py
  python3 scripts/infra/toggle_ip.py --force
  python3 scripts/infra/toggle_ip.py --check
"""

import os
import sys
import argparse

# 프로젝트 루트 경로 자동 추가
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from services.infra.ip_toggle import ip_toggle_mgr, MIN_COOLDOWN_SECONDS
except ImportError:
    from services.ip_toggle import ip_toggle_mgr, MIN_COOLDOWN_SECONDS


def main():
    parser = argparse.ArgumentParser(description="MikroTik WAN IP Toggle & Health Check Tool (Isolated)")
    parser.add_argument("--force", "-f", action="store_true", help="10분 쿨다운을 무시하고 강제 토글")
    parser.add_argument("--timeout", "-t", type=int, default=90, help="IP 할당 및 복구 최대 대기 시간(초) (기본: 90)")
    parser.add_argument("--check", "-c", action="store_true", help="현재 WAN IP 및 쿨다운 상태만 확인")
    args = parser.parse_args()

    if args.check:
        wan_info = ip_toggle_mgr.get_wan_info()
        ext_ip = ip_toggle_mgr.get_public_ip_external()
        state = ip_toggle_mgr.load_state()
        is_ready, rem = ip_toggle_mgr.check_cooldown()

        print("\n" + "=" * 60)
        print("🌐 CURRENT IP & TOGGLE STATUS:")
        print("=" * 60)
        print(f"• Router WAN IP   : {wan_info.get('ip')} ({wan_info.get('status')})")
        print(f"• Router WAN MAC  : {wan_info.get('mac')}")
        print(f"• External Pub IP : {ext_ip or 'UNKNOWN'}")
        print(f"• Total Toggles   : {state.get('toggle_count', 0)}회")
        print(f"• Last Old/New IP : {state.get('last_old_ip')} ➜ {state.get('last_new_ip')}")
        print(f"• 10m Cooldown    : {'🟢 READY' if is_ready else f'⏳ WAITING ({rem // 60}m {rem % 60}s 남음)'}")
        print("=" * 60 + "\n")
        return

    res = ip_toggle_mgr.toggle_ip(force=args.force, timeout_sec=args.timeout)

    print("\n" + "=" * 60)
    print("🔄 IP TOGGLE RESULT:")
    print("=" * 60)
    print(f"• Success         : {res.get('success')}")
    if res.get("success"):
        print(f"• Previous IP     : {res.get('old_ip')}")
        print(f"• New IP          : {res.get('new_ip')}")
        print(f"• New MAC         : {res.get('new_mac')}")
        print(f"• Elapsed Time    : {res.get('duration_sec')}s")
        print(f"• IP Changed      : {'YES' if res.get('ip_changed') else 'SAME (ISP Assigned Same Subnet)'}")
    else:
        print(f"• Reason          : {res.get('reason')}")
        print(f"• Message         : {res.get('message', 'N/A')}")
        if res.get("remaining_seconds"):
            rem = res["remaining_seconds"]
            print(f"• Cooldown Left   : {rem // 60}분 {rem % 60}초 (강제 실행은 --force 플래그 사용)")
    print("=" * 60 + "\n")

    if not res.get("success") and res.get("reason") != "COOLDOWN_ACTIVE":
        sys.exit(1)


if __name__ == "__main__":
    main()
