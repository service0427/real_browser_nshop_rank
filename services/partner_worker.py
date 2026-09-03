import asyncio
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional

from core.logger import get_logger
from config.settings import TASK_QUEUE_SERVER, DEFAULT_LEASE_SECONDS
from services.crawler import crawl_shopping_rank_async

logger = get_logger("rank.partner.worker")


def print_jq(title: str, data: Any, is_request: bool = False):
    """jq 스타일 예쁜 JSON 콘솔 출력 포맷터"""
    border = "=" * 80 if is_request else "-" * 80
    header_icon = "📤 [API REQUEST]" if is_request else "📥 [API RESPONSE]"
    print(f"\n{border}")
    print(f"{header_icon} {title}")
    print(border)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(str(data))
    print(f"{border}\n")


class TechBPartnerWorker:
    """
    TechB 분산 태스크 큐 워커 클라이언트 (API_PARTNER_GUIDE.md 스펙 100% 호환)
    """

    def __init__(self, server_url: str = TASK_QUEUE_SERVER, lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self.server_url = server_url.rstrip("/")
        self.lease_seconds = lease_seconds
        self.lease_endpoint = f"{self.server_url}/api/v1/task"
        self.return_endpoint = f"{self.server_url}/api/v1/task/return"

    def fetch_task(self, service: str = "shop") -> Optional[Dict[str, Any]]:
        """
        1. 작업 가져오기 (Task Lease)
        GET /api/v1/task?service=shop&lease_seconds=300
        """
        params = urllib.parse.urlencode({
            "service": service,
            "worker": "pc",
            "lease_seconds": self.lease_seconds
        })
        url = f"{self.lease_endpoint}?{params}"

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TechB-Worker/2.3.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    raw_text = resp.read().decode("utf-8")
                    data = json.loads(raw_text)
                    if data.get("success") and data.get("has_task"):
                        print_jq(f"GET {url}", data, is_request=False)
                        return data
        except urllib.error.URLError as e:
            logger.warning(f"[Lease] 태스크 서버 연결 대기 중 ({self.server_url}): {e}")
        except Exception as e:
            logger.error(f"[Lease] 태스크 조회 중 오류 발생: {e}")

        return None

    def return_task_result(
        self,
        task_id: int,
        service: str,
        rank: Optional[int] = None,
        product: Optional[Dict[str, Any]] = None,
        is_blocked: bool = False,
        error_message: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        2. 순위 결과 제출 및 오류 반환 (Task Return)
        POST /api/v1/task/return
        """
        payload: Dict[str, Any] = {
            "task_id": task_id,
            "service": service
        }

        # Case 3: 차단 / 오류 발생 시
        if is_blocked or error_message:
            payload["status"] = "BLOCKED"
            payload["error_message"] = error_message or "차단 또는 알 수 없는 오류 발생"
        # Case 1: 순위 포착 (정상 발견)
        elif rank and rank > 0 and product:
            payload["rank"] = rank
            payload["product"] = {
                "productName": product.get("productName") or product.get("productTitle") or "",
                "mallName": product.get("mallName") or "네이버",
                "lowPrice": product.get("lowPrice") or product.get("price") or 0,
                "imageUrl": product.get("imageUrl") or "",
                "reviewCount": product.get("reviewCount") or 0,
                "scoreInfo": product.get("scoreInfo") or 0.0,
                "nvMid": product.get("id") or product.get("nvMid") or "",
                "brand": product.get("brand") or "",
                "category": product.get("category") or ""
            }
        # Case 2: 순위권 밖 (정상 0위 확정)
        else:
            payload["rank"] = 0

        # 전송할 요청 JSON 콘솔 출력 (jq 형식)
        print_jq(f"POST {self.return_endpoint}", payload, is_request=True)

        req_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.return_endpoint,
            data=req_data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "TechB-Worker/2.3.0"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                # 응답 수신 JSON 콘솔 출력 (jq 형식)
                print_jq(f"POST {self.return_endpoint} -> Result", resp_json, is_request=False)
                logger.info(f"[Return] Task #{task_id} 결과 반환 완료: {resp_json.get('message')}")
                return resp_json
        except Exception as e:
            logger.error(f"[Return] Task #{task_id} 결과 전송 실패: {e}")
            return {"success": False, "error": str(e)}

    async def execute_task(self, task: Dict[str, Any], max_pages: int = 25, headless: bool = False) -> Dict[str, Any]:
        """
        단일 태스크 처리 파이프라인 (기본 25페이지 / 1000위)
        """
        task_id = task.get("task_id")
        keyword = task.get("keyword")
        target_id = task.get("target")
        service = task.get("service", "shop")

        keyword_total_count = task.get("keyword_total_count", 1)
        use_keyword_cache = (keyword_total_count >= 2)
        logger.info(f"▶ [Task #{task_id}] 작업 시작: 키워드='{keyword}', 타겟='{target_id}', 큐내동일키워드수={keyword_total_count} (캐시저장: {'ON' if use_keyword_cache else 'OFF'})")

        try:
            crawl_res = await crawl_shopping_rank_async(
                keyword=keyword,
                target_id=target_id,
                max_pages=max_pages,
                headless=headless,
                use_keyword_cache=use_keyword_cache,
                active_workers=1
            )

            # 정상 완료 (200 OK)
            if crawl_res.get("status") == 200:
                if crawl_res.get("targetFound"):
                    target_rank = crawl_res.get("targetRank")
                    target_product = crawl_res.get("targetProduct")
                    matched_field = crawl_res.get("matchedField", "item.id")
                    
                    logger.info(f"★ [Task #{task_id}] 타겟 발견! #{target_rank}위 [{target_product.get('productTypeName')}] (매칭필드: {matched_field})")
                    self.return_task_result(
                        task_id=task_id,
                        service=service,
                        rank=target_rank,
                        product=target_product
                    )
                else:
                    logger.info(f"★ [Task #{task_id}] 탐색 완료 (1~{max_pages}페이지 밖 / 200위 밖 0위 확정)")
                    self.return_task_result(
                        task_id=task_id,
                        service=service,
                        rank=0
                    )
                return {"success": True, "is_blocked": False, "rank": crawl_res.get("targetRank", 0)}

            # 실패 (차단 또는 2회 재시도 초과)
            else:
                err_msg = crawl_res.get("error") or crawl_res.get("errorMessage") or "네이버 차단 또는 수집 실패"
                logger.error(f"🚨 [Task #{task_id}] 수집 실패/차단 감지: {err_msg}")
                self.return_task_result(
                    task_id=task_id,
                    service=service,
                    is_blocked=True,
                    error_message=err_msg
                )
                return {"success": False, "is_blocked": True, "error": err_msg}

        except Exception as e:
            err_msg = str(e)
            logger.error(f"🚨 [Task #{task_id}] 예외 발생: {err_msg}", exc_info=True)
            self.return_task_result(
                task_id=task_id,
                service=service,
                is_blocked=True,
                error_message=err_msg
            )
            return {"success": False, "is_blocked": True, "error": err_msg}

    async def run_worker_loop(
        self,
        service: str = "shop",
        poll_interval: int = 5,
        max_pages: int = 5,
        headless: bool = False,
        max_loops: Optional[int] = None
    ):
        """
        프로덕션 무인 워커 루프 (기본 5페이지 / 200위)
        """
        logger.info(f"==========================================================")
        logger.info(f"🚀 TechB 실서비스 분산 태스크 큐 워커 구동 시작")
        logger.info(f"• 태스크 서버   : {self.server_url}")
        logger.info(f"• 대상 서비스   : {service.upper()}")
        logger.info(f"• 최대 반복 횟수: {'무한 루프' if max_loops is None else f'{max_loops}회 제한'}")
        logger.info(f"• 폴링 주기     : {poll_interval}초")
        logger.info(f"• 탐색 최대 깊이: {max_pages}페이지 (최대 1000위)")
        logger.info(f"• 차단 방어 룰  : 차단 감지 시 즉시 전체 사이클 완전 종료")
        logger.info(f"==========================================================")

        processed_count = 0

        while True:
            try:
                task = self.fetch_task(service=service)
                if task:
                    result = await self.execute_task(task, max_pages=max_pages, headless=headless)
                    
                    # [핵심 룰 1] 차단으로 실패한 경우 전체 사이클 완전 종료
                    if result.get("is_blocked"):
                        logger.error("\n" + "!" * 75)
                        logger.error(f"🚨 [치명적 차단 감지] 네이버 차단 발생으로 워커 사이클을 즉시 완전 종료합니다.")
                        logger.error("!" * 75 + "\n")
                        sys.exit(1)

                    processed_count += 1
                    logger.info(f"✔ [진행 상황] 완료된 작업: {processed_count}개" + (f" / {max_loops}개" if max_loops else ""))

                    # [핵심 룰 2] --loop 제한 수 도달 시 정상 종료
                    if max_loops is not None and processed_count >= max_loops:
                        logger.info(f"\n🎉 지정된 {max_loops}개 작업 처리가 모두 완료되어 워커를 정상 종료합니다.")
                        break

                else:
                    await asyncio.sleep(poll_interval)

            except SystemExit:
                break
            except Exception as e:
                logger.error(f"[Worker Loop Error]: {e}")
                await asyncio.sleep(poll_interval)


partner_worker = TechBPartnerWorker()
