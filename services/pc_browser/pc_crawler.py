"""
services/pc_browser/pc_crawler.py
PC 리얼 브라우저 (Stage 2) 전용 고속 크롤러
- 세션당 4회 페이징 쿼터 활용: 1~5페이지(200위까지) 100% 무손실 JSON 수집 (20초 소요)
- 네이버 모바일 통합검색 -> 가격비교 더보기 -> 상단 고정 페이징 버튼 물리 클릭
"""

import os
import sys
import time
import asyncio
from typing import Dict, Any, Optional, List
from playwright.async_api import async_playwright

from core.logger import get_logger
from services.profile_pool import ProfilePoolManager
from services.keyword_cache import keyword_cache_mgr
from services.rank_logger import RankLogger
from services.block_logger import Block418Logger
from services.data_extractor import DataExtractor
from services.dom_navigator import DOMNavigator
from services.pc_browser.browser_process import BrowserProcessManager
from services.pc_browser.cdp_controller import CDPController

logger = get_logger("pc_browser.crawler")


async def crawl_pc_rank_async(
    keyword: str,
    target_id: str,
    max_pages: int = 5,
    port: int = 9201,
    headless: bool = False,
    use_keyword_cache: bool = False,
    profile_mgr: Optional[ProfilePoolManager] = None,
    active_workers: int = 1,
    include_raw_products: bool = False
) -> Dict[str, Any]:
    """
    [Stage 2] PC 리얼 브라우저 전용 1~5페이지(1위~200위) 고속 수집기
    """
    start_time = time.time()

    # 1. 스마트 키워드 캐시 우선 조회 (0.0001초)
    if use_keyword_cache:
        cached_result = keyword_cache_mgr.lookup(keyword, target_id)
        if cached_result.get("hit"):
            return {
                "status": 200,
                "stage": 2,
                "targetFound": cached_result.get("found", False),
                "targetRank": cached_result.get("rank"),
                "targetProduct": cached_result.get("product"),
                "matchedFieldName": cached_result.get("matchedField"),
                "pagesCrawled": cached_result.get("maxPage", 1),
                "bytesReceived": 0,
                "kbReceived": 0.0,
                "engine": "Keyword_SmartCache_JSON",
                "usedProfile": "MEMORY_CACHE",
                "elapsedSec": round(time.time() - start_time, 4)
            }
        elif cached_result.get("reason") == "PARTIAL_CACHE" and cached_result.get("cachedMaxPage", 0) >= max_pages:
            logger.info(f"⚡ [PC Stage 2 | 캐시 {max_pages}p(200위) 탐색 완료] 키워드='{keyword}' -> 타겟='{target_id}' 200위 밖 (0초 즉시 0위 반환)")
            return {
                "status": 200,
                "stage": 2,
                "targetFound": False,
                "targetRank": 0,
                "targetProduct": None,
                "matchedFieldName": None,
                "pagesCrawled": cached_result.get("cachedMaxPage", max_pages),
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

    # 3. 우분투 Real GUI Chrome 브라우저 프로세스 실행
    chrome_proc = BrowserProcessManager.launch(port=port, profile_path=profile_path, headless=headless)
    await asyncio.sleep(2.0)

    # PC 버전은 세션당 418 차단 발생 전 안전하게 5페이지(200위)까지 100% JSON 수집
    max_pages = min(max_pages, 5)

    all_organic_products: List[Dict[str, Any]] = []
    seen_ids = set()
    target_found = False
    target_rank = 0
    target_product = None
    matched_field = None
    target_page = 1
    cdp_ctrl: Optional[CDPController] = None
    captured_jsons: List[Dict[str, Any]] = []
    api_response_event = asyncio.Event()

    try:
        async with async_playwright() as p:
            browser = None
            for _ in range(15):
                try:
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
                    break
                except Exception:
                    await asyncio.sleep(0.8)
            if not browser:
                raise RuntimeError(f"Chrome CDP connection timed out on port {port}")

            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            # 네트워크 JSON 인터셉터 (오직 실제 상품 검색 결과 JSON만 수신)
            async def on_response(res):
                url = res.url
                if ("api/search/all" in url or "_next/data" in url) and res.status == 200:
                    ct = res.headers.get("content-type", "")
                    if "json" in ct:
                        try:
                            data = await res.json()
                            if isinstance(data, dict):
                                captured_jsons.append(data)
                                api_response_event.set()
                        except Exception:
                            pass

            page.on("response", on_response)

            # 4. CDP 세션 및 세로형 모바일 뷰포트 주입
            cdp_ctrl = CDPController(page)
            await cdp_ctrl.setup_session()

            # 5. 모바일 통합검색 진입 및 쇼핑 이동
            api_response_event.clear()
            await DOMNavigator.navigate_to_search(page, keyword)
            try:
                await asyncio.wait_for(api_response_event.wait(), timeout=3.5)
            except asyncio.TimeoutError:
                pass

            # 6. 1페이지 오가닉 상품 추출 (100% JSON)
            p1_raw = []
            if captured_jsons:
                for json_data in captured_jsons:
                    raw_list = (
                        json_data.get("shoppingResult", {}).get("products", [])
                        or json_data.get("products", {}).get("list", [])
                        or json_data.get("compositeProducts", {}).get("list", [])
                    )
                    for item in raw_list:
                        item_dict = item.get("item", item)
                        if not isinstance(item_dict, dict):
                            continue
                        if item.get("isAd") or item.get("ad") or item_dict.get("isAd") or item_dict.get("ad") or item_dict.get("adId"):
                            continue
                        p1_raw.append(item_dict)
                    if p1_raw:
                        break

            if not p1_raw:
                next_data = await DataExtractor.extract_next_data(page)
                if next_data:
                    p1_raw, _ = DataExtractor.parse_products_from_next_data(next_data)

            if p1_raw:
                for idx, item in enumerate(p1_raw):
                    cur_rank = idx + 1
                    p_id = str(item.get("id") or item.get("nvMid") or item.get("channelProductId") or item.get("productTitle") or cur_rank)
                    if p_id not in seen_ids:
                        seen_ids.add(p_id)
                        item["rank"] = cur_rank
                        all_organic_products.append(item)

                    if target_id and not target_found:
                        match_res = DataExtractor.match_target(item, target_id)
                        if match_res:
                            matched_val, matched_field = match_res
                            target_found = True
                            target_rank = cur_rank
                            target_page = 1
                            target_product = DataExtractor.format_product_info(item, matched_val)
                            logger.info(f"★ [PC Stage 2] 1p에서 타겟 발견: #{target_rank}위 ({matched_field}={matched_val})")
                            break

            # 7. 2~5페이지(200위) 고속 수집 (브라우저 내부 Next.js Data API fetch)
            if not target_found and max_pages > 1:
                import urllib.parse
                encoded_kw = urllib.parse.quote(keyword)
                
                # buildId 획득
                if not next_data:
                    next_data = await DataExtractor.extract_next_data(page)
                build_id = next_data.get("buildId") if next_data else ""

                for cur_p in range(2, max_pages + 1):
                    new_items = []
                    
                    # 1순위: 브라우저 내부 세션 기반 초고속 API fetch (0.5초, 1~5p 418 차단 0건)
                    if build_id:
                        data_url = f"/_next/data/{build_id}/search/all.json?query={encoded_kw}&pagingIndex={cur_p}&pagingSize=40"
                        fetch_res = await page.evaluate("""async (url) => {
                            try {
                                const r = await window.fetch(url, {
                                    method: 'GET',
                                    headers: { 'x-nextjs-data': '1', 'accept': '*/*' },
                                    credentials: 'include'
                                });
                                if (r.status === 200) return { status: 200, json: await r.json() };
                                return { status: r.status };
                            } catch(e) { return { err: e.message }; }
                        }""", data_url)

                        if fetch_res.get("status") == 200:
                            p_list, _ = DataExtractor.parse_products_from_next_data(fetch_res.get("json", {}))
                            if p_list:
                                new_items = p_list
                                logger.info(f"✔ [PC Stage 2 API] {cur_p}p 고속 Data Fetch 성공 ({len(p_list)}개 상품)")
                        else:
                            logger.warning(f"⚠️ [PC Stage 2 API] {cur_p}p fetch 응답: {fetch_res.get('status')} -> DOM 클릭 폴백 전환")

                    # 2순위 폴백: API 실패 또는 buildId 부재 시 실제 DOM 클릭
                    if not new_items:
                        captured_jsons.clear()
                        api_response_event.clear()
                        clicked = await DOMNavigator.click_next_page(page, cur_p)
                        if not clicked:
                            break
                        try:
                            await asyncio.wait_for(api_response_event.wait(), timeout=2.5)
                        except asyncio.TimeoutError:
                            await asyncio.sleep(1.2)

                        for json_data in captured_jsons:
                            raw_list = (
                                json_data.get("shoppingResult", {}).get("products", [])
                                or json_data.get("products", {}).get("list", [])
                                or json_data.get("products", [])
                                or json_data.get("compositeProducts", {}).get("list", [])
                            )
                            for item in raw_list:
                                item_dict = item.get("item", item)
                                if not isinstance(item_dict, dict):
                                    continue
                                if item.get("isAd") or item.get("ad") or item_dict.get("isAd") or item_dict.get("ad") or item_dict.get("adId"):
                                    continue
                                new_items.append(item_dict)

                    fresh_count = sum(1 for item in new_items if str(item.get("id") or item.get("nvMid") or item.get("channelProductId") or item.get("productTitle") or "") not in seen_ids)
                    if fresh_count == 0:
                        logger.warning(f"⚠️ [PC Stage 2] {cur_p}p 이동 실패 (새 상품 0건) -> 페이징 중단")
                        break

                    for idx, item in enumerate(new_items):
                        cur_rank = len(all_organic_products) + 1
                        p_id = str(item.get("id") or item.get("nvMid") or item.get("channelProductId") or item.get("productTitle") or cur_rank)
                        if p_id not in seen_ids:
                            seen_ids.add(p_id)
                            item["rank"] = item.get("rank") or cur_rank
                            all_organic_products.append(item)

                        if target_id and not target_found:
                            match_res = DataExtractor.match_target(item, target_id)
                            if match_res:
                                matched_val, matched_field = match_res
                                target_found = True
                                target_rank = item.get("rank") or cur_rank
                                target_page = cur_p
                                target_product = DataExtractor.format_product_info(item, matched_val)
                                logger.info(f"★ [PC Stage 2] {cur_p}p에서 타겟 발견: #{target_rank}위 ({matched_field}={matched_val})")
                                break

                    if target_found:
                        break

        # 8. 스마트 캐시 저장 (동일 키워드 재사용 대비)
        actual_pages = target_page if target_found else (len(all_organic_products) + 39) // 40
        if all_organic_products:
            keyword_cache_mgr.update(keyword, all_organic_products, actual_pages)
            RankLogger.save_keyword_ranks(keyword, all_organic_products)

        elapsed = round(time.time() - start_time, 2)
        bytes_rec = cdp_ctrl.bytes_received if cdp_ctrl else 0
        is_success = bool(all_organic_products)

        # 프로필 풀 성공 릴리즈
        if profile_mgr and profile_id:
            profile_mgr.report_result(profile_id, success=is_success, is_login_or_block=not is_success)

        if not is_success:
            Block418Logger.record_abnormal(
                event_type="NO_PRODUCTS_FETCHED",
                keyword=keyword,
                target_id=target_id or "",
                page=actual_pages,
                worker="pc",
                device_or_profile=profile_name,
                error_message="상품 0개 수신 (차단 의심)",
                elapsed_sec=elapsed
            )

        return {
            "status": 200 if is_success else 500,
            "stage": 2,
            "targetFound": target_found,
            "targetRank": target_rank if target_found else (0 if is_success else None),
            "targetProduct": target_product,
            "matchedFieldName": matched_field,
            "pagesCrawled": actual_pages,
            "totalProductsCrawled": len(all_organic_products),
            "bytesReceived": bytes_rec,
            "kbReceived": round(bytes_rec / 1024, 2),
            "engine": "PC_RealBrowser_Stage2",
            "usedProfile": profile_name,
            "products": all_organic_products if include_raw_products else None,
            "elapsedSec": elapsed
        }

    except Exception as e:
        logger.error(f"[PC Stage 2] 크롤링 오류 발생: {e}")
        Block418Logger.record_abnormal(
            event_type="EXCEPTION_ERROR",
            keyword=keyword,
            target_id=target_id or "",
            page=1,
            worker="pc",
            device_or_profile=profile_name,
            error_message=str(e),
            elapsed_sec=round(time.time() - start_time, 2)
        )
        if profile_mgr and profile_id:
            profile_mgr.report_result(profile_id, success=False, is_login_or_block=True)
        return {
            "status": 500,
            "stage": 2,
            "error": str(e),
            "targetFound": False,
            "targetRank": None,
            "targetProduct": None,
            "pagesCrawled": 0,
            "bytesReceived": 0,
            "kbReceived": 0.0,
            "engine": "PC_RealBrowser_Stage2",
            "usedProfile": profile_name,
            "elapsedSec": round(time.time() - start_time, 2)
        }
    finally:
        await BrowserProcessManager.terminate(chrome_proc, port=port)


# 기존 crawl_shopping_rank_async 호환성 alias
crawl_shopping_rank_async = crawl_pc_rank_async
