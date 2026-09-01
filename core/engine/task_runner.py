"""
core/engine/task_runner.py
단일 워커의 작업 처리 라이프사이클 (임대 -> 즉시 재할당/캐시/크롤링 -> 결과 반환 -> 캐시 Purge) 관리 모듈
"""

import time
import asyncio
from typing import Dict, Any, Optional, Set
from core.logger import get_logger
from services.partner_worker import partner_worker as api_worker
from services.profile_pool import ProfilePoolManager
from services.keyword_cache import keyword_cache_mgr
from services.crawler import crawl_shopping_rank_async

logger = get_logger("engine.task_runner")


class TaskRunner:
    """단일 워커의 태스크 실행기"""

    @staticmethod
    async def process_task(
        worker_id: int,
        port: int,
        pool_mgr: ProfilePoolManager,
        in_flight_keywords: Set[str],
        lock: asyncio.Lock,
        headless: bool = False
    ) -> Optional[Dict[str, Any]]:
        """단일 태스크를 임대받아 처리하고 결과 반환 및 통계 딕셔너리 리턴"""
        # 1. 태스크 임대 (Lease)
        task = api_worker.fetch_task(service="shop")
        if not task:
            return None

        loop_start = time.time()
        task_id = task.get("task_id")
        keyword = task.get("keyword")
        target_id = task.get("target")
        keyword_total_count = task.get("keyword_total_count", 1)
        keyword_remaining_count = task.get("keyword_remaining_count", 1)
        total_remaining_tasks = task.get("total_remaining_tasks", 0)
        use_keyword_cache = (keyword_total_count >= 2)

        rem_str = f" [남은상품: {keyword_remaining_count}/{keyword_total_count}개 | 전체큐잔여: {total_remaining_tasks:,}개]" if total_remaining_tasks else f" [동일키워드: {keyword_total_count}개]"
        logger.info(f"▶ [W{worker_id} | Task #{task_id}] 키워드='{keyword}', 타겟='{target_id}'{rem_str} (캐시: {'ON' if use_keyword_cache else 'OFF'})")

        # 2. [Non-blocking Fast Requeue: 비행 중 키워드 즉시 재할당 엔진]
        is_in_flight = False
        if use_keyword_cache:
            async with lock:
                if keyword in in_flight_keywords:
                    is_in_flight = True
                else:
                    in_flight_keywords.add(keyword)

        if is_in_flight:
            logger.info(f"🔄 [W{worker_id} | 0초 즉시 반납] '{keyword}' 타 쓰레드가 선행 탐색 중 -> 브라우저 유휴 대기 없이 큐 맨 뒤로 반납 후 다음 작업 즉시 진행!")
            api_worker.return_task_result(
                task_id=task_id,
                service="shop",
                is_blocked=True,
                error_message=f"[{keyword}] 타 쓰레드 선행 탐색 중 -> 캐시 활용을 위해 큐 맨 뒤로 재할당"
            )
            return {"requeued": True, "task_id": task_id, "keyword": keyword}

        # 3. 크롤링 수행
        try:
            crawl_res = await crawl_shopping_rank_async(
                keyword=keyword,
                target_id=target_id,
                max_pages=25,
                port=port,
                headless=headless,
                use_keyword_cache=use_keyword_cache,
                profile_mgr=pool_mgr
            )
        finally:
            if use_keyword_cache:
                async with lock:
                    in_flight_keywords.discard(keyword)

        is_success = crawl_res.get("status") == 200
        is_blocked = not is_success
        rank = crawl_res.get("targetRank", 0) if is_success else None
        bytes_received = crawl_res.get("bytesReceived", 0)
        kb_received = crawl_res.get("kbReceived", 0.0)
        elapsed_sec = crawl_res.get("elapsedSec", round(time.time() - loop_start, 2))
        is_cache_hit = (crawl_res.get("engine") == "Keyword_SmartCache_JSON")

        # 4. 서버 결과 반환
        if is_success:
            api_worker.return_task_result(
                task_id=task_id,
                service="shop",
                rank=rank,
                product=crawl_res.get("targetProduct")
            )
        else:
            api_worker.return_task_result(
                task_id=task_id,
                service="shop",
                is_blocked=True,
                error_message=crawl_res.get("error", "NAVER_BLOCK_OR_ERROR")
            )

        # 5. 캐시 라이프사이클 관리: 마지막 상품(remaining=1) 처리 후 즉시 Purge
        if use_keyword_cache and keyword_remaining_count == 1:
            keyword_cache_mgr.purge(keyword)

        return {
            "task_id": task_id,
            "keyword": keyword,
            "target_id": target_id,
            "status": crawl_res.get("status"),
            "is_success": is_success,
            "is_blocked": is_blocked,
            "rank": rank,
            "target_found": crawl_res.get("targetFound", False),
            "cache_hit": is_cache_hit,
            "used_profile": crawl_res.get("usedProfile"),
            "bytes_received": bytes_received,
            "kb_received": kb_received,
            "elapsed_sec": elapsed_sec,
            "timestamp": int(time.time()),
            "total_remaining_tasks": total_remaining_tasks
        }
