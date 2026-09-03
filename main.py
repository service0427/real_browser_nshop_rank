#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Naver Organic Ranking Unified CLI, Worker & Daemon Entrypoint (Shop & Place).

Usage Examples:
1. Shopping Rank (Real Chrome Nest Hub SSR, 1~25 Pages / 1000 Ranks):
   python main.py shop --keyword "노트북" --target 52631236642
   python main.py shop --keyword "무선이어폰" --maxpage 25

2. TechB Distributed Task Queue Worker (API_PARTNER_GUIDE.md):
   python main.py worker --service shop --loop 5
   python main.py worker --server http://114.207.112.172:9003

3. Place Rank:
   python main.py place --keyword "강남역 맛집" --target 1047144456

4. Start API Server:
   python main.py api --port 9003
"""

import os
import sys

# [자동 가상환경 감지 및 전환] 사용자가 어떤 python으로 실행하든 전용 venv로 자동 실행
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_CANDIDATES = [
    os.path.join(SCRIPT_DIR, "venv", "bin", "python3"),
    os.path.expanduser("~/venv/bin/python3"),
]
for venv_py in VENV_CANDIDATES:
    if os.path.exists(venv_py) and os.path.abspath(sys.executable) != os.path.abspath(venv_py):
        os.execv(venv_py, [venv_py] + sys.argv)

sys.path.insert(0, SCRIPT_DIR)

import argparse
from config.settings import TASK_QUEUE_SERVER, API_PORT


def handle_shop(args):
    from services.crawler import crawl_shopping_rank_async
    import asyncio
    result = asyncio.run(crawl_shopping_rank_async(
        keyword=args.keyword,
        target_id=args.target,
        max_pages=args.maxpage,
        headless=args.headless
    ))
    print("\n" + "=" * 80)
    print("SHOP RANKING RESULT:")
    print("=" * 80)
    print(f"Status          : {result.get('status')}")
    print(f"Keyword         : '{args.keyword}'")
    print(f"Engine          : {result.get('engine')}")
    print(f"Elapsed Time    : {result.get('elapsedSec', 0):.2f}s")
    print(f"Pages Crawled   : {result.get('pagesCrawled', 0)} pages")
    print(f"Total Products  : {result.get('totalProductsCrawled', 0)} items")
    print(f"Rank List File  : {result.get('rankFilePath', 'N/A')}")
    print(f"Traffic (KB)    : {result.get('kbReceived', 0.0):.2f} KB")

    if args.target:
        print("\n" + "★" * 50)
        print(f"Target ID       : {args.target}")
        print(f"Target Found    : {result.get('targetFound')}")
        if result.get('targetFound'):
            tp = result.get('targetProduct', {})
            print(f"Target Rank     : #{result.get('targetRank')}위")
            print(f"Matched Field   : [{result.get('matchedFieldName', 'item.id')}]")
            print(f"Product Title   : {tp.get('productName') or tp.get('productTitle')}")
            print(f"Mall Name       : {tp.get('mallName')}")
            print(f"Price           : {tp.get('lowPrice', 0):,}원")
            print(f"item.id (nvMid) : {tp.get('id') or tp.get('nvMid')}")
        else:
            print("Target Result   : Product not found within search boundary (Rank 0).")
        print("★" * 50)
    print("=" * 80)


def handle_worker(args):
    threads = getattr(args, "threads", 4)
    from core.engine.supervisor import ClusterSupervisor
    import asyncio
    supervisor = ClusterSupervisor(max_threads=threads, headless=args.headless)
    asyncio.run(supervisor.run())


def handle_place(args):
    try:
        from legacy.place.runner import get_place_rank_sync
        result = get_place_rank_sync(
            keyword=args.keyword,
            target_id=args.target,
            max_pages=args.maxpage,
            headless=args.headless,
            block_media=not args.no_block_media,
            proxy_url=args.proxy
        )
    except Exception as e:
        print(f"❌ Place crawler is in legacy: {e}")
        return

    print("\n" + "=" * 80)
    print("PLACE RANKING RESULT:")
    print("=" * 80)
    print(f"Status          : {result.get('status')}")
    print(f"Keyword         : '{result.get('keyword')}'")
    print(f"Total Time      : {result.get('totalTime', result.get('elapsedSec', 0)):.2f}s")
    print(f"Total Extracted : {result.get('totalExtracted')} places")

    if args.target:
        print("\n" + "★" * 50)
        print(f"Target Place ID : {args.target}")
        print(f"Target Found    : {result.get('targetFound')}")
        if result.get('targetFound'):
            print(f"Target Rank     : #{result.get('targetRank')}")
        print("★" * 50)
    print("=" * 80)


def handle_toggle(args):
    try:
        from services.infra.ip_toggle import ip_toggle_mgr
    except ImportError:
        try:
            from services.ip_toggle import ip_toggle_mgr
        except ImportError:
            print("❌ IP toggle module not found.")
            return
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


def handle_api(args):
    import uvicorn
    from config.settings import API_HOST
    port = args.port or API_PORT
    uvicorn.run("api_server:app", host=API_HOST, port=port, reload=False, workers=1)


def main():
    parser = argparse.ArgumentParser(description="TechB Naver Organic Rank CLI, Worker & API Entrypoint")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # 1. Shop Command
    p_shop = subparsers.add_parser("shop", help="Real-time shopping rank query")
    p_shop.add_argument("--keyword", "-k", required=True, help="Search keyword")
    p_shop.add_argument("--target", "-t", default=None, help="Target product nvMid or channelProductId")
    p_shop.add_argument("--maxpage", "-m", type=int, default=5, help="Max pages (default: 5 / 200 ranks)")
    p_shop.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    # 2. Worker Command (TechB Task Queue Client)
    p_worker = subparsers.add_parser("worker", help="TechB Distributed Task Queue Multi-Worker")
    p_worker.add_argument("--threads", "-T", type=int, default=4, help="Number of concurrent worker threads (default: 4, up to 8)")
    p_worker.add_argument("--service", "-s", default="shop", choices=["shop", "place"], help="Target service queue")
    p_worker.add_argument("--server", default=None, help=f"Task queue server URL (default: {TASK_QUEUE_SERVER})")
    p_worker.add_argument("--interval", "-i", type=int, default=5, help="Polling interval in seconds (default: 5)")
    p_worker.add_argument("--lease", "-l", type=int, default=300, help="Lease lock seconds (default: 300)")
    p_worker.add_argument("--maxpage", "-m", type=int, default=5, help="Max search pages (default: 5 / 200 ranks)")
    p_worker.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    # 3. Place Command
    p_place = subparsers.add_parser("place", help="Real-time place rank query")
    p_place.add_argument("--keyword", "-k", required=True, help="Search keyword")
    p_place.add_argument("--target", "-t", default=None, help="Target place ID")
    p_place.add_argument("--maxpage", "-m", type=int, default=12, help="Max scroll depth (default: 12)")
    p_place.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    p_place.add_argument("--no-block-media", action="store_true", help="Do not block media/images")
    p_place.add_argument("--proxy", default=None, help="Custom proxy URL")

    # 4. Toggle Command (MikroTik WAN IP Toggle)
    p_toggle = subparsers.add_parser("toggle", help="MikroTik Router WAN IP Toggle & Health Check")
    p_toggle.add_argument("--force", "-f", action="store_true", help="Force toggle ignoring 10-minute cooldown")
    p_toggle.add_argument("--timeout", "-t", type=int, default=90, help="Timeout in seconds (default: 90)")
    p_toggle.add_argument("--check", "-c", action="store_true", help="Check current IP and cooldown status only")

    # 5. API Command
    p_api = subparsers.add_parser("api", help="Start FastAPI Server")
    p_api.add_argument("--port", "-p", type=int, default=None, help="API server port")

    args = parser.parse_args()

    if args.command == "shop":
        handle_shop(args)
    elif args.command == "worker":
        handle_worker(args)
    elif args.command == "place":
        handle_place(args)
    elif args.command == "toggle":
        handle_toggle(args)
    elif args.command == "api":
        handle_api(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
