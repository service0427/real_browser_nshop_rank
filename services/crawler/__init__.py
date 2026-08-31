"""
services/crawler/__init__.py
모듈화된 고속 리얼 브라우저 크롤링 파이프라인 Facade
"""

import time
import asyncio
from typing import Dict, Any, Optional, List
from playwright.async_api import async_playwright

from core.logger import get_logger
from services.profile_pool import ProfilePoolManager
from services.keyword_cache import keyword_cache_mgr
from services.crawler.browser_process import BrowserProcessManager
from services.crawler.cdp_controller import CDPController
from services.crawler.dom_navigator import DOMNavigator
from services.crawler.data_extractor import DataExtractor

logger = get_logger("crawler.pipeline")


async def crawl_shopping_rank_async(
    keyword: str,
    target_id: str,
    max_pages: int = 10,
    port: int = 9201,
    headless: bool = False,
    use_keyword_cache: bool = False,
    profile_mgr: Optional[ProfilePoolManager] = None
) -> Dict[str, Any]:
    """
    모듈화된 통합 쇼핑 순위 수집기 (0.0001초 캐시 조회 -> 모바일 통검 -> 10p 페이징 완주)
    """
    start_time = time.time()

    # 1. 스마트 키워드 캐시 우선 조회
    if use_keyword_cache:
        cached_result = keyword_cache_mgr.lookup(keyword, target_id)
        if cached_result.get("hit"):
            return {
                "status": 200,
                "targetFound": cached_result.get("found", False),
                "targetRank": cached_result.get("rank"),
                "targetProduct": cached_result.get("product"),
                "matchedFieldName": cached_result.get("matchedField"),
                "pagesCrawled": 0,
                "bytesReceived": 0,
                "kbReceived": 0.0,
                "engine": "Keyword_SmartCache_JSON",
                "usedProfile": "MEMORY_CACHE",
                "elapsedSec": round(time.time() - start_time, 4)
            }

    # 2. 프로필 할당
    profile_info = profile_mgr.acquire_next_profile() if profile_mgr else {}
    profile_path = profile_info.get("path")
    profile_name = profile_info.get("name", "default_profile")
    profile_id = profile_info.get("id", 101)

    # 3. Chrome 브라우저 프로세스 실행
    chrome_proc = BrowserProcessManager.launch(port=port, profile_path=profile_path, headless=headless)
    await asyncio.sleep(2.0)

    all_organic_products: List[Dict[str, Any]] = []
    target_found = False
    target_rank = 0
    target_product = None
    matched_field = None
    target_page = 1
    cdp_ctrl: Optional[CDPController] = None

    try:
        async with async_playwright() as p:
            browser = None
            for _ in range(5):
                try:
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
                    break
                except Exception:
                    await asyncio.sleep(0.6)
            if not browser:
                raise RuntimeError(f"Chrome CDP connection timed out on port {port}")

            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            # 4. CDP 세션 및 에뮬레이션 주입
            cdp_ctrl = CDPController(page)
            await cdp_ctrl.setup_session(port=port)

            # 5. 모바일 통합검색 진입
            await DOMNavigator.navigate_to_search(page, keyword)

            # 6. 1페이지 ~ 10페이지 순회
            for current_page in range(1, max_pages + 1):
                if current_page > 1:
                    nav_ok = await DOMNavigator.click_next_page(page, current_page)
                    if not nav_ok:
                        break

                next_data = None
                for _ in range(2):
                    next_data = await DataExtractor.extract_next_data(page)
                    if next_data:
                        break
                    await asyncio.sleep(1.0)

                if not next_data:
                    logger.warning(f"[{current_page}페이지] NEXT_DATA 미검출 -> 차단 의심")
                    break

                page_products, ad_cnt = DataExtractor.parse_products_from_next_data(next_data)
                logger.info(f"✔ [{current_page}페이지 수집 성공] {len(all_organic_products)+1}위 ~ {len(all_organic_products)+len(page_products)}위 ({len(page_products)}개 오가닉, 광고 {ad_cnt}개)")

                # 타겟 상품 실시간 매칭
                for idx, item in enumerate(page_products):
                    current_rank = len(all_organic_products) + idx + 1
                    match_res = DataExtractor.match_target(item, target_id)
                    if match_res:
                        matched_val, matched_field = match_res
                        target_found = True
                        target_rank = current_rank
                        target_page = current_page
                        target_product = DataExtractor.format_product_info(item, matched_val)
                        logger.info(f"★ 타겟 상품 발견: #{target_rank}위 ({target_product.get('productName')}) -> 즉시 조기 종료")
                        break

                all_organic_products.extend(page_products)
                if target_found or len(page_products) < 10:
                    break

            await context.close()

        # 7. 캐시 저장
        if use_keyword_cache and all_organic_products:
            keyword_cache_mgr.update(keyword, all_organic_products, max_page_crawled=target_page)

        # 8. 자가치유 프로필 보고
        if profile_mgr:
            profile_mgr.report_result(profile_id, success=bool(all_organic_products), is_login_or_block=not bool(all_organic_products))

        elapsed = round(time.time() - start_time, 2)
        return {
            "status": 200 if all_organic_products else 500,
            "targetFound": target_found,
            "targetRank": target_rank if target_found else (0 if all_organic_products else None),
            "targetProduct": target_product,
            "matchedFieldName": matched_field,
            "pagesCrawled": target_page,
            "bytesReceived": cdp_ctrl.total_bytes if cdp_ctrl else 0,
            "kbReceived": cdp_ctrl.total_kb if cdp_ctrl else 0.0,
            "engine": "Modular_CDP_RealBrowser",
            "usedProfile": profile_name,
            "elapsedSec": elapsed
        }

    except Exception as e:
        logger.error(f"크롤링 중 예외 발생: {e}")
        if profile_mgr:
            profile_mgr.report_result(profile_id, success=False, is_login_or_block=True)
        return {
            "status": 500,
            "error": str(e),
            "targetFound": False,
            "targetRank": None,
            "bytesReceived": cdp_ctrl.total_bytes if cdp_ctrl else 0,
            "kbReceived": cdp_ctrl.total_kb if cdp_ctrl else 0.0,
            "engine": "Modular_CDP_RealBrowser",
            "usedProfile": profile_name,
            "elapsedSec": round(time.time() - start_time, 2)
        }
    finally:
        await BrowserProcessManager.terminate(chrome_proc, port)
