"""
services/phone_farm/phone_crawler.py
실제 안드로이드 기기(폰팜) 전용 Stage 3 심층 크롤러 (1~25페이지 완주, api/search/all 200 OK JSON 인터셉트)
"""

import os
import sys
import time
import asyncio
import random
import urllib.parse
from typing import Dict, Any, Optional, List
from playwright.async_api import async_playwright

from core.logger import get_logger
from services.phone_farm.device_manager import phone_device_mgr, PhoneDevice
from services.rank_logger import RankLogger
from services.data_extractor import DataExtractor
from services.keyword_cache import keyword_cache_mgr
from services.block_logger import Block418Logger

logger = get_logger("phone_farm.crawler")




async def crawl_phone_rank_async(
    keyword: str,
    target_id: Optional[str] = None,
    max_pages: int = 25,
    worker_id: int = 1,
    use_keyword_cache: bool = False,
    include_raw_products: bool = False
) -> Dict[str, Any]:
    """
    [Stage 3] 실기기 폰팜 정밀 순위 및 메타데이터 수집
    """
    start_time = time.time()

    # 0. 키워드 캐시 사전 조회 (동일 키워드 2개 이상일 때 폰 구동 없이 0초 즉각 반환)
    if use_keyword_cache:
        cached_res = keyword_cache_mgr.lookup(keyword, target_id)
        if cached_res.get("hit"):
            if cached_res.get("found"):
                logger.info(f"⚡ [Stage 3 | 캐시 적중] 키워드='{keyword}', 타겟='{target_id}' -> #{cached_res['rank']}위 (폰 구동 없이 0초 반환)")
                return {
                    "status": 200,
                    "stage": 3,
                    "targetFound": True,
                    "targetRank": cached_res["rank"],
                    "targetProduct": cached_res.get("product"),
                    "matchedFieldName": cached_res.get("matchedField"),
                    "pagesCrawled": cached_res.get("maxPage", 1),
                    "totalProductsCrawled": cached_res.get("cachedCount", 0),
                    "engine": "Keyword_SmartCache_JSON",
                    "elapsedSec": 0.001
                }
            elif cached_res.get("maxPage", 1) >= max_pages:
                logger.info(f"⚡ [Stage 3 | 캐시 1,000위 전수 조사 완료] 키워드='{keyword}' -> 타겟 없음 (0위 즉시 반환)")
                return {
                    "status": 200,
                    "stage": 3,
                    "targetFound": False,
                    "targetRank": 1001,
                    "targetProduct": None,
                    "matchedFieldName": None,
                    "pagesCrawled": cached_res.get("maxPage", 25),
                    "totalProductsCrawled": cached_res.get("cachedCount", 0),
                    "engine": "Keyword_SmartCache_JSON",
                    "elapsedSec": 0.001
                }
        elif cached_res.get("reason") == "PARTIAL_CACHE" and cached_res.get("cachedMaxPage", 0) >= max_pages:
            logger.info(f"⚡ [Stage 3 | 캐시 {max_pages}p(1,000위) 전수 조사 완료] 키워드='{keyword}' -> 타겟 없음 (0위 즉시 반환)")
            return {
                "status": 200,
                "stage": 3,
                "targetFound": False,
                "targetRank": 1001,
                "targetProduct": None,
                "matchedFieldName": None,
                "pagesCrawled": cached_res.get("cachedMaxPage", 25),
                "totalProductsCrawled": cached_res.get("cachedCount", 0),
                "engine": "Keyword_SmartCache_JSON",
                "elapsedSec": 0.001
            }

    # 1. 폰팜 기기 임대
    device = await phone_device_mgr.acquire_device(worker_id=worker_id)
    if not device:
        return {
            "status": 503,
            "stage": 3,
            "error": "NO_IDLE_PHONE_DEVICE_AVAILABLE",
            "targetFound": False,
            "targetRank": None,
            "elapsedSec": round(time.time() - start_time, 2)
        }

    all_products: List[Dict[str, Any]] = []
    seen_ids = set()
    target_found = False
    target_rank = 0
    target_product = None
    matched_field = None
    target_page = 1
    captured_jsons: List[Dict[str, Any]] = []

    try:
        async with async_playwright() as p:
            browser = None
            for cdp_attempt in range(1, 3):
                try:
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{device.cdp_port}", timeout=8000)
                    break
                except Exception as e:
                    if cdp_attempt == 1:
                        logger.warning(f"⚠️ [폰팜 {device.serial}] CDP 연결 실패 ({e}) -> 크롬 프로세스 재기동 후 재시도...")
                        phone_device_mgr.restart_device_chrome(device)
                        await asyncio.sleep(1.5)
                    else:
                        raise e

            context = browser.contexts[0] if browser.contexts else await browser.new_context()

            # 항상 깨끗한 단 1개의 활성 화면 탭만 유지
            new_page = await context.new_page()
            for old_p in list(context.pages):
                if old_p != new_page:
                    try:
                        await old_p.close()
                    except Exception:
                        pass
            page = new_page
            await page.bring_to_front()

            # 크롬 크래시("앗, 이런!") 감지 및 자동 새로고침 복구
            try:
                crash_check = await page.evaluate("() => document.body ? document.body.innerText : ''")
                if "앗, 이런" in crash_check or "문제가 발생했습니다" in crash_check:
                    logger.warning(f"🚨 [폰팜 {device.serial}] 크롬 크래시('앗, 이런!') 감지 -> 자동 새로고침 복구 수행")
                    await page.reload(wait_until="domcontentloaded", timeout=10000)
                    await asyncio.sleep(1.5)
            except Exception:
                pass

            # CDP Target.activateTarget으로 안드로이드 화면에 탭 전면 활성화
            try:
                cdp_session = await context.new_cdp_session(page)
                target_info = await cdp_session.send("Target.getTargetInfo")
                if target_info and target_info.get("targetInfo", {}).get("targetId"):
                    await cdp_session.send("Target.activateTarget", {"targetId": target_info["targetInfo"]["targetId"]})
            except Exception:
                pass

            api_response_event = asyncio.Event()
            detected_429 = [False]

            # 네트워크 JSON 인터셉터 (수신 즉시 이벤트 트리거 및 HTTP 429 감지)
            async def on_response(res):
                url = res.url
                if res.status == 418:
                    logger.warning(f"🚨 [폰팜 {device.serial}] HTTP 418 차단 감지 ({url})")
                    Block418Logger.record_abnormal(
                        event_type="418_BLOCKED",
                        keyword=keyword,
                        target_id=target_id or "",
                        page=last_crawled_page,
                        worker="mobile",
                        device_or_profile=device.serial,
                        error_message=f"HTTP 418 on {url}",
                        elapsed_sec=round(time.time() - start_time, 2)
                    )
                elif res.status == 429:
                    detected_429[0] = True
                    logger.warning(f"⚠️ [폰팜 {device.serial}] HTTP 429 감지 ({url})")
                if ("api/search/all" in url or "_next/data" in url) and res.status == 200:
                    ct = res.headers.get("content-type", "")
                    if "json" in ct:
                        try:
                            data = await res.json()
                            captured_jsons.append(data)
                            api_response_event.set()
                        except Exception:
                            pass

            page.on("response", on_response)

            async def handle_429_retry(cur_page_num: int = 1) -> bool:
                """429 에러 감지 시 30초 대기 후 새로고침 최대 2회 재시도"""
                if not detected_429[0]:
                    return True
                for retry in range(1, 3):
                    logger.warning(f"⏳ [폰팜 {device.serial}] HTTP 429 감지 -> 30초 대기 후 새로고침 재시도 ({retry}/2회)...")
                    await asyncio.sleep(30.0)
                    detected_429[0] = False
                    captured_jsons.clear()
                    api_response_event.clear()
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.5)
                        if cur_page_num > 1:
                            await DOMNavigator.click_next_page(page, cur_page_num)
                        try:
                            await asyncio.wait_for(api_response_event.wait(), timeout=4.0)
                        except asyncio.TimeoutError:
                            pass
                    except Exception as e:
                        logger.error(f"[폰팜 {device.serial}] 새로고침 중 오류: {e}")

                    if not detected_429[0] and (captured_jsons or cur_page_num == 1):
                        logger.info(f"✔ [폰팜 {device.serial}] 30초 대기 후 429 해제 및 정상 복구 성공!")
                        return True
                logger.error(f"❌ [폰팜 {device.serial}] 429 2회 재시도 모두 실패")
                return False

            # 2. 통검 진입 및 공식 더보기 클릭
            from services.dom_navigator import DOMNavigator
            logger.info(f"📱 [Stage 3 | Phone {device.serial}] 통검 진입 및 쇼핑 이동: {keyword}")
            api_response_event.clear()
            await DOMNavigator.navigate_to_search(page, keyword)

            # 1페이지 API 응답 수신 대기 (도착 즉시 0.001초만에 wakeup, 최대 4.0초 대기)
            try:
                await asyncio.wait_for(api_response_event.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                pass

            # 1페이지 429 감지 시 30초 후 새로고침 재시도
            if detected_429[0]:
                await handle_429_retry(1)

            # 4. 1페이지 상품 추출 (API shoppingResult.products 및 NEXT_DATA 전수 파싱)
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
                        all_products.append(item)

                    if target_id and not target_found:
                        match_res = DataExtractor.match_target(item, target_id)
                        if match_res:
                            matched_val, matched_field = match_res
                            target_found = True
                            target_rank = cur_rank
                            target_page = 1
                            target_product = DataExtractor.format_product_info(item, matched_val)
                            logger.info(f"★ [Stage 3] 1p에서 타겟 발견: #{target_rank}위")
                            break

            # 5. 2페이지 ~ max_pages 순회 (페이지 이동 감지 및 리스트 갱신 검증 적용)
            last_crawled_page = 1
            pagination_failed = False

            if not target_found and max_pages > 1:
                for cur_p in range(2, max_pages + 1):
                    page_change_success = False
                    new_items = []

                    # 페이지당 최대 2회 시도 (1차 실패 시 JS 강제 디스패치 재시도)
                    for attempt in range(1, 3):
                        captured_jsons.clear()
                        api_response_event.clear()

                        clicked = await DOMNavigator.click_next_page(page, cur_p)
                        if not clicked:
                            logger.info(f"[Stage 3] {cur_p}페이지 클릭 불가 -> 마지막 페이지 도달")
                            break

                        # API 수신 즉시 웨이크업 (최대 3.5초 타임아웃: 모바일 무선망 레이턴시 고려)
                        try:
                            await asyncio.wait_for(api_response_event.wait(), timeout=3.5)
                        except asyncio.TimeoutError:
                            pass

                        # 페이징 중 429 감지 시 30초 후 새로고침 재시도
                        if detected_429[0]:
                            ok = await handle_429_retry(cur_p)
                            if not ok:
                                break

                        # 1. API / _next/data JSON 인터셉트 검증
                        if captured_jsons:
                            latest = captured_jsons[-1]
                            raw_list = (
                                latest.get("shoppingResult", {}).get("products", [])
                                or latest.get("products", {}).get("list", [])
                                or latest.get("products", [])
                                or latest.get("compositeProducts", {}).get("list", [])
                                or latest.get("pageProps", {}).get("initialState", {}).get("products", {}).get("list", [])
                                or latest.get("pageProps", {}).get("compositeProducts", {}).get("list", [])
                            )
                            for item in raw_list:
                                item_dict = item.get("item", item)
                                if not isinstance(item_dict, dict):
                                    continue
                                # 광고 상품 필터링
                                if item.get("isAd") or item.get("ad") or item_dict.get("isAd") or item_dict.get("ad") or item_dict.get("adId"):
                                    continue
                                new_items.append(item_dict)

                        # 2. DOM React Fiber 카드 폴백 검증 (API 미수신 시)
                        if not new_items:
                            try:
                                dom_cards = await DataExtractor.extract_dom_card_products(page)
                                for item in dom_cards:
                                    p_id = str(item.get("id") or item.get("nvMid") or item.get("channelProductId") or item.get("productTitle") or "")
                                    if p_id and p_id not in seen_ids:
                                        new_items.append(item)
                            except Exception as e:
                                logger.debug(f"DOM 카드 폴백 추출 오류: {e}")

                        # 3. [페이지 이동 탐지 및 rank 연속성 검증]
                        first_rank = None
                        for p in new_items:
                            if isinstance(p.get("rank"), int):
                                first_rank = p.get("rank")
                                break

                        expected_min_rank = (cur_p - 1) * 35
                        fresh_count = sum(1 for item in new_items if str(item.get("id") or item.get("nvMid") or item.get("channelProductId") or item.get("productTitle") or "") not in seen_ids)

                        if fresh_count > 0 and (first_rank is None or first_rank >= expected_min_rank):
                            page_change_success = True
                            break
                        else:
                            logger.warning(f"⚠️ [폰팜 {device.serial}] {cur_p}p 이동 미감지 (fresh={fresh_count}, first_rank={first_rank}, 예상최소={expected_min_rank}, 시도 {attempt}/2회) -> 재시도")
                            await asyncio.sleep(1.0)

                    # 2회 시도 후에도 실패 시: 억지 재시도 없이 기기 오류로 즉시 실패 처리하고 타 기기 인계!
                    if not page_change_success:
                        logger.warning(f"🚨 [폰팜 {device.serial}] {cur_p}p 페이지 이동 실패 (rank/리스트 미변경 감지) -> 패스(Requeue) 및 타 기기 인계")
                        pagination_failed = True
                        Block418Logger.record_abnormal(
                            event_type="PAGINATION_FAILED",
                            keyword=keyword,
                            target_id=target_id or "",
                            page=cur_p,
                            worker="mobile",
                            device_or_profile=device.serial,
                            error_message=f"{cur_p}p 페이징 이동 미감지 (fresh={fresh_count})",
                            elapsed_sec=round(time.time() - start_time, 2)
                        )
                        break

                    # 신규 상품들 누적 및 타겟 매칭
                    for idx, item in enumerate(new_items):
                        cur_rank = len(all_products) + 1
                        p_id = str(item.get("id") or item.get("nvMid") or item.get("channelProductId") or item.get("productTitle") or cur_rank)
                        if p_id not in seen_ids:
                            seen_ids.add(p_id)
                            all_products.append(item)

                        if target_id and not target_found:
                            match_res = DataExtractor.match_target(item, target_id)
                            if match_res:
                                matched_val, matched_field = match_res
                                target_found = True
                                target_rank = item.get("rank") or cur_rank
                                target_page = cur_p
                                target_product = DataExtractor.format_product_info(item, matched_val)
                                logger.info(f"★ [Stage 3] {cur_p}p에서 타겟 발견: #{target_rank}위 ({matched_field}={matched_val})")
                                break

                    last_crawled_page = cur_p
                    if target_found:
                        break

        # 6. 순위 목록 파일 저장 및 키워드 캐시 갱신
        rank_file_path = ""
        actual_pages = target_page if target_found else ((len(all_products) + 39) // 40)
        if all_products:
            rank_file_path = RankLogger.save_keyword_ranks(keyword, all_products)
            keyword_cache_mgr.update(keyword, all_products, max_page_crawled=actual_pages)

        elapsed = round(time.time() - start_time, 2)

        # [안전 검증]
        # 타겟을 찾은 경우: 즉시 해당 랭크 반환
        # 타겟을 못 찾았고 max_pages(25p)까지 완주한 경우: 1001(최종 0위 확정) 반환
        # 중간에 페이징 실패로 3~4p에서 멈춘 경우: 1001로 오판하지 않고 500 오류 반환하여 서버가 Requeue하도록 함!
        if target_found:
            is_success = True
            final_target_rank = target_rank
        elif actual_pages >= max_pages or (not pagination_failed and actual_pages >= 20):
            is_success = True
            final_target_rank = 1001
        else:
            is_success = False
            final_target_rank = None
            logger.error(f"🚨 [Stage 3 | 폰팜 {device.serial}] {keyword}: {actual_pages}p에서 페이징 중단 ({max_pages}p 미도달) -> 0위 오판 방지를 위해 REQUEUE(500) 처리")
            Block418Logger.record_abnormal(
                event_type="INCOMPLETE_PAGING",
                keyword=keyword,
                target_id=target_id or "",
                page=actual_pages,
                worker="mobile",
                device_or_profile=device.serial,
                error_message=f"25p 미도달 ({actual_pages}p 페이징 중단)",
                elapsed_sec=elapsed
            )

        return {
            "status": 200 if is_success else 500,
            "stage": 3,
            "deviceSerial": device.serial,
            "deviceModel": device.model,
            "keyword": keyword,
            "targetFound": target_found,
            "targetRank": final_target_rank,
            "targetProduct": target_product,
            "matchedFieldName": matched_field,
            "pagesCrawled": actual_pages,
            "totalProductsCrawled": len(all_products),
            "rankFilePath": rank_file_path,
            "products": all_products if include_raw_products else None,
            "elapsedSec": elapsed
        }

    except Exception as e:
        logger.error(f"[Stage 3] 폰 크롤링 오류: {e}")
        Block418Logger.record_abnormal(
            event_type="EXCEPTION_ERROR",
            keyword=keyword,
            target_id=target_id or "",
            page=1,
            worker="mobile",
            device_or_profile=getattr(device, "serial", "unknown") if 'device' in locals() and device else "no_device",
            error_message=str(e),
            elapsed_sec=round(time.time() - start_time, 2)
        )
        return {
            "status": 500,
            "stage": 3,
            "deviceSerial": device.serial,
            "error": str(e),
            "targetFound": False,
            "targetRank": None,
            "elapsedSec": round(time.time() - start_time, 2)
        }
    finally:
        await phone_device_mgr.release_device(device)
