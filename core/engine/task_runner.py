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
from services.pc_browser import crawl_pc_rank_async

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
        headless: bool = False,
        active_workers: int = 1,
        default_stage: int = 3
    ) -> Optional[Dict[str, Any]]:
        """단일 태스크를 임대받아 처리하고 결과 반환 및 통계 딕셔너리 리턴"""
        # 1. 태스크 임대 (Lease) - Stage 2: 'pc', Stage 3: 'mobile'
        worker_param = "mobile" if default_stage == 3 else "pc"
        task = api_worker.fetch_task(service="shop", worker=worker_param)
        if not task and default_stage == 3:
            # 🛡️ 기기 유휴 방지 Auto-Fallback: mobile 전용 대기열 소진 시 pc 대기열 작업 즉시 인수
            task = api_worker.fetch_task(service="shop", worker="pc")
            if task:
                worker_param = "pc->mobile"
        if not task:
            return None

        loop_start = time.time()
        task_id = task.get("task_id")
        keyword = task.get("keyword")
        target_id = task.get("target")
        keyword_total_count = task.get("keyword_total_count", 1)
        keyword_remaining_count = task.get("keyword_remaining_count", 1)
        total_remaining_tasks = task.get("total_remaining_tasks", 0)
        use_keyword_cache = True

        stage = default_stage
        max_pages = 25 if stage == 3 else task.get("max_pages", 5)

        rem_str = f" [남은상품: {keyword_remaining_count}/{keyword_total_count}개 | 전체큐잔여: {total_remaining_tasks:,}개]" if total_remaining_tasks else f" [동일키워드: {keyword_total_count}개]"
        logger.info(f"▶ [W{worker_id} | Task #{task_id}] 키워드='{keyword}', 타겟='{target_id}'{rem_str} (캐시: ON, Worker: {worker_param})")

        # 2. 캐시 사전 확인 및 In-Flight Fast Requeue 검사
        cached_check = keyword_cache_mgr.lookup(keyword, target_id)
        has_instant_cache = cached_check.get("hit") or (
            cached_check.get("reason") == "PARTIAL_CACHE" and cached_check.get("cachedMaxPage", 0) >= max_pages
        )

        if not has_instant_cache:
            async with lock:
                if keyword in in_flight_keywords:
                    api_worker.return_task_result(
                        task_id=task_id,
                        service="shop",
                        is_blocked=True,
                        error_message="IN_FLIGHT_FAST_REQUEUE"
                    )
                    return {"requeued": True, "task_id": task_id, "keyword": keyword}
                in_flight_keywords.add(keyword)

        # 3. 크롤링 수행 (Stage 2: PC 고속 5p, Stage 3: 폰팜 25p)
        try:
            if stage == 3:
                from services.phone_farm import crawl_phone_rank_async
                crawl_res = await crawl_phone_rank_async(
                    keyword=keyword,
                    target_id=target_id,
                    max_pages=max_pages,
                    worker_id=worker_id,
                    use_keyword_cache=use_keyword_cache
                )
            else:
                crawl_res = await crawl_pc_rank_async(
                    keyword=keyword,
                    target_id=target_id,
                    max_pages=max_pages,
                    port=port,
                    headless=headless,
                    use_keyword_cache=use_keyword_cache,
                    profile_mgr=pool_mgr,
                    active_workers=active_workers
                )
        finally:
            if not has_instant_cache:
                async with lock:
                    in_flight_keywords.discard(keyword)

        is_success = crawl_res.get("status") == 200
        is_blocked = not is_success
        target_found = crawl_res.get("targetFound", False)
        worker_type = "mobile" if stage == 3 else "pc"

        if is_success:
            rank = crawl_res.get("targetRank") if target_found else (1001 if stage == 3 else 0)
        else:
            rank = None

        bytes_received = crawl_res.get("bytesReceived", 0)
        kb_received = crawl_res.get("kbReceived", 0.0)
        elapsed_sec = crawl_res.get("elapsedSec", round(time.time() - loop_start, 2))
        is_cache_hit = (crawl_res.get("engine") == "Keyword_SmartCache_JSON")

        # 4. 서버 결과 반환 (AGENTS.md 명세 준수)
        if is_success:
            api_worker.return_task_result(
                task_id=task_id,
                service="shop",
                worker=worker_type,
                rank=rank,
                product=crawl_res.get("targetProduct")
            )
        else:
            api_worker.return_task_result(
                task_id=task_id,
                service="shop",
                worker=worker_type,
                is_blocked=True,
                error_message=crawl_res.get("error", "NAVER_BLOCK_OR_ERROR")
            )

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
