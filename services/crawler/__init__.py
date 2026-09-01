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
from services.rank_logger import RankLogger
from services.block_logger import Block418Logger
from services.crawler.browser_process import BrowserProcessManager
from services.crawler.cdp_controller import CDPController
from services.crawler.dom_navigator import DOMNavigator
from services.crawler.data_extractor import DataExtractor

logger = get_logger("crawler.pipeline")


async def crawl_shopping_rank_async(
    keyword: str,
    target_id: str,
    max_pages: int = 25,
    port: int = 9201,
    headless: bool = False,
    use_keyword_cache: bool = False,
    profile_mgr: Optional[ProfilePoolManager] = None,
    active_workers: int = 1
) -> Dict[str, Any]:
    """
    모듈화된 통합 쇼핑 순위 수집기 (0.0001초 캐시 조회 -> 모바일 통검 -> 25p 페이징 완주)
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

            # 4. CDP 세션 및 에뮬레이션 주입
            cdp_ctrl = CDPController(page)
            await cdp_ctrl.setup_session()

            # 5. 모바일 통합검색 진입 및 1페이지 수집
            await DOMNavigator.navigate_to_search(page, keyword)
            await asyncio.sleep(2.0)

            # 1페이지 SSR __NEXT_DATA__ 파싱
            nd1 = await DataExtractor.extract_next_data(page)
            build_id = nd1.get("buildId") if nd1 else None
            p1_products, p1_ad_cnt = DataExtractor.parse_products_from_next_data(nd1) if nd1 else ([], 0)

            seen_mids = set()
            if p1_products:
                logger.info(f"✔ [1페이지 수집 성공] 1위 ~ {len(p1_products)}위 ({len(p1_products)}개 오가닉, 광고 {p1_ad_cnt}개)")
                for idx, item in enumerate(p1_products):
                    current_rank = idx + 1
                    mid = str(item.get("id") or item.get("nvMid") or "")
                    if mid:
                        seen_mids.add(mid)
                    match_res = DataExtractor.match_target(item, target_id)
                    if match_res:
                        matched_val, matched_field = match_res
                        target_found = True
                        target_rank = current_rank
                        target_page = 1
                        target_product = DataExtractor.format_product_info(item, matched_val)
                        logger.info(f"★ 타겟 상품 발견: #{target_rank}위 ({target_product.get('productName')}) -> 즉시 조기 종료")
                        break
                all_organic_products.extend(p1_products)

            crawl_error = None
            # 6. 2페이지 ~ 25페이지(최대 1000위) 순차 수집 (Next.js Data API + 5페이지 단위 지능형 토큰 체이닝)
            if not target_found and max_pages > 1 and all_organic_products:
                import urllib.parse
                encoded_kw = urllib.parse.quote(keyword)
                await asyncio.sleep(1.5)  # 1페이지 진입 후 WTM 세션 토큰 안정화 대기

                for current_page in range(2, max_pages + 1):
                    # 5페이지 단위 청크 전환 시점 (6p, 11p, 16p, 21p)
                    # WTM 토큰 소진 전 모바일 통검 재경유로 새 세션 토큰 & build_id 사전 자동 갱신
                    if (current_page - 1) % 5 == 0:
                        logger.info(f"🔑 [{current_page}페이지 진입] 5페이지 청크 토큰 갱신: 모바일 통검 경유하여 새 세션 토큰 사전 발급...")
                        await DOMNavigator.navigate_to_search(page, keyword)
                        await asyncio.sleep(2.5)  # 토큰 발급 대기
                        nd_chunk = await DataExtractor.extract_next_data(page)
                        if nd_chunk and nd_chunk.get("buildId"):
                            build_id = nd_chunk.get("buildId")
                        await asyncio.sleep(1.0)

                    data_url = f"/_next/data/{build_id}/search/all.json?query={encoded_kw}&pagingIndex={current_page}&pagingSize=40"
                    
                    page_products = []
                    ad_cnt = 0
                    
                    for attempt in range(3):
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

                        st_code = fetch_res.get("status", 0)
                        if st_code == 200:
                            page_products, ad_cnt = DataExtractor.parse_products_from_next_data(fetch_res.get("json", {}))
                            if page_products:
                                break
                        
                        # 418 차단 히스토리 일자별 전용 저장
                        if st_code == 418 or st_code == 0:
                            Block418Logger.record_418(
                                keyword=keyword,
                                page=current_page,
                                attempt=attempt + 1,
                                profile_name=profile_name,
                                active_workers=active_workers,
                                consecutive_success_pages=target_page,
                                request_url=data_url,
                                status_code=st_code,
                                extra_reason="WTM_418_RATE_LIMIT" if st_code == 418 else "NETWORK_OR_DISCONNECT"
                            )

                        # 418 또는 실패 시 브라우저 세션 재갱신
                        logger.info(f"🔄 [{current_page}페이지] 세션 갱신 및 재시도 (시도 {attempt+1}/3, status: {st_code})")
                        await DOMNavigator.navigate_to_search(page, keyword)
                        await asyncio.sleep(2.5)
                        
                        nd_refresh = await DataExtractor.extract_next_data(page)
                        if nd_refresh and nd_refresh.get("buildId"):
                            build_id = nd_refresh.get("buildId")
                        data_url = f"/_next/data/{build_id}/search/all.json?query={encoded_kw}&pagingIndex={current_page}&pagingSize=40"
                        await asyncio.sleep(1.0)

                    if not page_products:
                        logger.warning(f"[{current_page}페이지] 상품 목록 비어있음 또는 수집 중단 (418 차단 감지)")
                        crawl_error = f"NAVER_BLOCKED_AT_PAGE_{current_page}"
                        Block418Logger.record_418(
                            keyword=keyword,
                            page=current_page,
                            attempt=3,
                            profile_name=profile_name,
                            active_workers=active_workers,
                            consecutive_success_pages=target_page,
                            request_url=data_url,
                            status_code=418,
                            extra_reason="EXHAUSTED_3_RETRIES"
                        )
                        break

                    # 1. 네이버 원본 item['rank'] 기반 페이징 연속성 검증
                    first_item_rank = None
                    for p in page_products:
                        if isinstance(p.get("rank"), int):
                            first_item_rank = p.get("rank")
                            break

                    expected_min_rank = (current_page - 1) * 35  # 광고 제외 감안한 최소 랭크 기준 (2페이지면 최소 35위 이상)
                    if first_item_rank is not None and current_page > 1 and first_item_rank < expected_min_rank:
                        logger.error(f"🚨 [{current_page}페이지] 네이버 원본 rank 불일치 감지 (첫 상품 item.rank={first_item_rank} < 예상최소 {expected_min_rank}) -> 418 미갱신 데이터로 판정 및 차단 처리")
                        crawl_error = f"STALE_RANK_SEQUENCE_AT_PAGE_{current_page}"
                        Block418Logger.record_418(
                            keyword=keyword,
                            page=current_page,
                            attempt=1,
                            profile_name=profile_name,
                            active_workers=active_workers,
                            consecutive_success_pages=target_page,
                            request_url=data_url,
                            status_code=418,
                            extra_reason=f"STALE_RANK_MISMATCH_FIRST_RANK_{first_item_rank}"
                        )
                        break

                    # 2. 중복 MID 검사: 이전 페이지와 중복 상품 다수 발생 시 418 미갱신 판정
                    new_mids = [str(p.get("id") or p.get("nvMid") or "") for p in page_products if (p.get("id") or p.get("nvMid"))]
                    dup_count = sum(1 for m in new_mids if m in seen_mids)
                    if new_mids and dup_count > (len(new_mids) * 0.4):
                        logger.error(f"🚨 [{current_page}페이지] 중복 상품 감지 ({dup_count}/{len(new_mids)}개) -> 418 미갱신 정적 데이터로 판정 및 차단 처리")
                        crawl_error = f"DUPLICATE_STALE_DATA_AT_PAGE_{current_page}"
                        Block418Logger.record_418(
                            keyword=keyword,
                            page=current_page,
                            attempt=1,
                            profile_name=profile_name,
                            active_workers=active_workers,
                            consecutive_success_pages=target_page,
                            request_url=data_url,
                            status_code=418,
                            extra_reason=f"DUPLICATE_MIDS_RATIO_{dup_count}_{len(new_mids)}"
                        )
                        break

                    for m in new_mids:
                        seen_mids.add(m)

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
                    target_page = current_page

                    if target_found:
                        break

                    await asyncio.sleep(1.0)

            await context.close()

        # 7. 순위 목록 파일 라인별 저장 (순위 | MID | 제목)
        rank_file_path = ""
        if all_organic_products:
            rank_file_path = RankLogger.save_keyword_ranks(keyword, all_organic_products)

        # 8. 캐시 저장 (타겟을 찾았거나 정상 완주 시에만)
        if use_keyword_cache and all_organic_products and (target_found or target_page >= max_pages):
            keyword_cache_mgr.update(keyword, all_organic_products, max_page_crawled=target_page)

        # 완주 및 정상 성공 여부 판별 (타겟 발견 or 목표 페이지 전수 완주)
        is_success = bool(all_organic_products) and (target_found or (target_page >= max_pages and not crawl_error))

        # 9. 자가치유 프로필 보고
        if profile_mgr:
            profile_mgr.report_result(profile_id, success=is_success, is_login_or_block=not is_success)

        elapsed = round(time.time() - start_time, 2)
        return {
            "status": 200 if is_success else 500,
            "error": None if is_success else (crawl_error or "INCOMPLETE_PAGING_BLOCKED"),
            "targetFound": target_found,
            "targetRank": target_rank if target_found else (0 if is_success else None),
            "targetProduct": target_product,
            "matchedFieldName": matched_field,
            "pagesCrawled": target_page,
            "rankFilePath": rank_file_path,
            "totalProductsCrawled": len(all_organic_products),
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
