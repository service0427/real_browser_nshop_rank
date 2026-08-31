"""
core/engine/supervisor.py
8-Thread 지원 멀티 워커 클러스터 슈퍼바이저, 10분 유휴 대기 모드 및 동시 차단 서킷 브레이커 모듈
"""

import os
import sys
import json
import time
import signal
import asyncio
import argparse
from typing import Dict, Any, List, Set, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.logger import get_logger
from services.profile_pool import ProfilePoolManager
from services.keyword_cache import keyword_cache_mgr
from core.engine.task_runner import TaskRunner

logger = get_logger("engine.supervisor")


class ClusterSupervisor:
    """고성능 N-Thread 크롤러 오케스트레이터 및 리소스 슈퍼바이저"""

    def __init__(self, max_threads: int = 4, headless: bool = False, idle_sleep_sec: int = 600):
        self.max_threads = max(1, min(max_threads, 16))
        self.headless = headless
        self.idle_sleep_sec = idle_sleep_sec  # 빈 큐 10분(600초) 유휴 대기
        
        self.results: List[Dict[str, Any]] = []
        self.worker_stats: Dict[int, Dict[str, Any]] = {
            w: {"processed": 0, "success": 0, "fail": 0, "cache_hits": 0, "traffic_bytes": 0}
            for w in range(1, self.max_threads + 1)
        }
        self.worker_last_status: Dict[int, str] = {w: "IDLE" for w in range(1, self.max_threads + 1)}
        self.in_flight_keywords: Set[str] = set()
        
        self.total_remaining_tasks: int = 0
        self.start_time: float = time.time()
        self.is_running: bool = True
        self.lock = asyncio.Lock()
        
        self.log_dir = os.path.join(BASE_DIR, "services", "runtime", "logs")
        os.makedirs(self.log_dir, exist_ok=True)

    async def write_status(self, is_finished: bool = False, finish_reason: str = ""):
        """실시간 진행 상태를 services/runtime/logs/drain_status.json 에 기록"""
        async with self.lock:
            total_tasks = len(self.results)
            success_count = sum(1 for r in self.results if r.get("is_success"))
            fail_count = total_tasks - success_count
            cache_hit_count = sum(1 for r in self.results if r.get("cache_hit"))
            real_crawl_count = total_tasks - cache_hit_count

            total_traffic_bytes = sum(r.get("bytes_received", 0) for r in self.results)
            total_traffic_mb = round(total_traffic_bytes / (1024 * 1024), 2)
            uptime_sec = round(time.time() - self.start_time, 2)
            avg_elapsed = round(sum(r.get("elapsed_sec", 0) for r in self.results) / total_tasks, 2) if total_tasks > 0 else 0

            status_data = {
                "is_running": self.is_running,
                "is_finished": is_finished,
                "finish_reason": finish_reason,
                "num_threads": self.max_threads,
                "total_remaining_tasks": self.total_remaining_tasks,
                "start_time": self.start_time,
                "current_time": time.time(),
                "uptime_sec": uptime_sec,
                "total_processed": total_tasks,
                "success_count": success_count,
                "fail_count": fail_count,
                "success_rate": round((success_count / total_tasks) * 100, 1) if total_tasks > 0 else 0.0,
                "cache_hit_count": cache_hit_count,
                "cache_hit_rate": round((cache_hit_count / total_tasks) * 100, 1) if total_tasks > 0 else 0.0,
                "real_crawl_count": real_crawl_count,
                "total_traffic_mb": total_traffic_mb,
                "avg_elapsed_sec": avg_elapsed,
                "cached_keywords_count": keyword_cache_mgr.count(),
                "worker_breakdown": self.worker_stats,
                "recent_tasks": self.results[-15:]
            }

            status_file = os.path.join(self.log_dir, "drain_status.json")
            try:
                with open(status_file, "w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"상태 파일 저장 실패: {e}")

    async def worker_loop(self, worker_id: int):
        """개별 워커 루프"""
        port = 9200 + worker_id
        start_idx = (worker_id * 100) + 1
        pool_mgr = ProfilePoolManager(worker_id=worker_id, start_id=start_idx, count=50)

        logger.info(f"🚀 [Worker #{worker_id}] 가동 준비 완료 | Port: {port} | 프로필: profile_{start_idx}~{start_idx+49}")

        while self.is_running:
            res = await TaskRunner.process_task(
                worker_id=worker_id,
                port=port,
                pool_mgr=pool_mgr,
                in_flight_keywords=self.in_flight_keywords,
                lock=self.lock,
                headless=self.headless
            )

            # 빈 큐인 경우
            if res is None:
                self.worker_last_status[worker_id] = "IDLE"
                logger.info(f"💤 [W{worker_id}] 작업이 없습니다. 서버 부하 방지를 위해 10초간 대기합니다.")
                await asyncio.sleep(10.0)
                continue

            # In-Flight 즉시 재할당된 경우 통계 건너뜀
            if res.get("requeued"):
                await asyncio.sleep(0.5)
                continue

            # 일반 크롤링 완료 처리
            async with self.lock:
                task_num = len(self.results) + 1
                res["task_num"] = task_num
                res["worker_id"] = worker_id
                self.results.append(res)

                if res.get("total_remaining_tasks", 0) > 0:
                    self.total_remaining_tasks = res["total_remaining_tasks"]

                # 워커별 통계
                w_stat = self.worker_stats[worker_id]
                w_stat["processed"] += 1
                if res["is_success"]:
                    w_stat["success"] += 1
                    self.worker_last_status[worker_id] = "OK"
                else:
                    w_stat["fail"] += 1
                    self.worker_last_status[worker_id] = "BLOCKED"

                if res.get("cache_hit"):
                    w_stat["cache_hits"] += 1
                w_stat["traffic_bytes"] += res.get("bytes_received", 0)

            # 콘솔 로그 출력
            status_icon = "🟢 성공" if res["is_success"] else "🔴 차단"
            rank_str = f"#{res['rank']}위" if res.get("rank") else ("0위(밖)" if res.get("target_found") is False and res["is_success"] else "N/A")
            cache_tag = " [⚡캐시]" if res.get("cache_hit") else ""
            logger.info(f"   ✔ [W{worker_id} | #{task_num:03d}] {status_icon}{cache_tag} | 키워드: '{res['keyword']}' | 순위: {rank_str} | 프로필: {res.get('used_profile')} | 소요: {res.get('elapsed_sec')}초")

            # 상태 JSON 갱신
            await self.write_status()

            # [안전 가드] 모든 워커가 동시에 차단(BLOCKED) 상태인지 검사
            async with self.lock:
                active_statuses = list(self.worker_last_status.values())
                if len(active_statuses) >= self.max_threads and all(s == "BLOCKED" for s in active_statuses):
                    logger.critical("🚨 [비상 정지] 모든 워커가 동시에 네이버 차단을 감지했습니다! IP 보호를 위해 자동 중지합니다.")
                    self.is_running = False
                    await self.write_status(is_finished=True, finish_reason="ALL_WORKERS_SIMULTANEOUSLY_BLOCKED")
                    break

            await asyncio.sleep(0.5)

    async def run(self):
        """전체 워커 클러스터 병렬 실행"""
        logger.info("=" * 80)
        logger.info(f"🚀 [TechB Modular Multi-Worker Supervisor]")
        logger.info(f"• 최대 쓰레드 수 : {self.max_threads}개 동시 실행")
        logger.info(f"• 모드         : {'헤드리스(Headless)' if self.headless else 'GUI 화면 표시(Screen)'}")
        logger.info(f"• 10분 유휴 대기: {self.idle_sleep_sec}초 설정")
        logger.info("=" * 80)

        tasks = [asyncio.create_task(self.worker_loop(w)) for w in range(1, self.max_threads + 1)]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            await self.write_status(is_finished=True, finish_reason="SUPERVISOR_STOPPED")


def main():
    parser = argparse.ArgumentParser(description="TechB 8-Thread Multi-Worker Supervisor")
    parser.add_argument("--threads", type=int, default=4, help="동시 실행 쓰레드 수 (1~8)")
    parser.add_argument("--headless", action="store_true", help="헤드리스 모드로 실행")
    parser.add_argument("--idle-sleep", type=int, default=600, help="빈 큐 유휴 대기 시간(초)")
    args = parser.parse_args()

    supervisor = ClusterSupervisor(max_threads=args.threads, headless=args.headless, idle_sleep_sec=args.idle_sleep)

    def sig_handler(sig, frame):
        logger.info("🛑 [종료 신호 수신] 워커 클러스터를 안전하게 종료합니다...")
        supervisor.is_running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        asyncio.run(supervisor.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
