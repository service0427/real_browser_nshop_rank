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
            await asyncio.sleep(3.5)

            async def extract_dom_products(p_page):
                return await p_page.evaluate("""() => {
                    const items = Array.from(document.querySelectorAll('li[class*="product_item"], div[class*="product_item"]'));
                    const results = [];
                    let adCnt = 0;
                    for (const el of items) {
                        const tEl = el.querySelector('strong, [class*="title"], [class*="tit"]');
                        const pEl = el.querySelector('em, [class*="price"], [class*="num"]');
                        const aEl = el.querySelector('a[data-nv-mid], a[href*="nvMid="], a[href*="products/"], a[href*="cr.shopping.naver.com"]');
                        const isAd = el.innerText.includes('광고') || !!el.querySelector('[class*="ad_"]');
                        
                        const title = tEl ? tEl.innerText.trim() : '';
                        const price = pEl ? pEl.innerText.trim().replace(/\\n/g, ' ') : '';
                        let nvMid = aEl ? (aEl.getAttribute('data-nv-mid') || '') : '';
                        if (!nvMid && aEl && aEl.href) {
                            const m1 = aEl.href.match(/nvMid=(\\d+)/);
                            const m2 = aEl.href.match(/products\\/(\\d+)/);
                            if (m1) nvMid = m1[1];
                            else if (m2) nvMid = m2[1];
                        }
                        if (title) {
                            if (isAd) {
                                adCnt++;
                            } else {
                                results.push({
                                    productTitle: title,
                                    title: title,
                                    name: title,
                                    price: price,
                                    lowPrice: price,
                                    nvMid: nvMid,
                                    id: nvMid,
                                    channelProductId: nvMid
                                });
                            }
                        }
                    }
                    return { products: results, adCount: adCnt };
                }""")

            p1_res = await extract_dom_products(page)
            p1_products = p1_res.get("products", [])
            p1_ad_cnt = p1_res.get("adCount", 0)

            seen_titles = set()
            if p1_products:
                logger.info(f"✔ [1페이지 수집 성공] 1위 ~ {len(p1_products)}위 ({len(p1_products)}개 오가닉, 광고 {p1_ad_cnt}개)")
                for idx, item in enumerate(p1_products):
                    current_rank = idx + 1
                    t = item.get("productTitle", "")
                    if t:
                        seen_titles.add(t)
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
            # 6. 2페이지 ~ max_pages 순차 수집 (시각적 하이라이트 + 1초 대기 + 리얼 물리 마우스 클릭)
            if not target_found and max_pages > 1 and all_organic_products:
                for current_page in range(2, max_pages + 1):
                    logger.info(f"👉 [{current_page}페이지 탐색 및 이동]")
                    clicked = await DOMNavigator.click_next_page(page, current_page)
                    if not clicked:
                        logger.warning(f"[{current_page}페이지] 페이징 버튼 클릭 실패 -> 수집 종료")
                        crawl_error = f"PAGING_CLICK_FAILED_AT_PAGE_{current_page}"
                        break

                    # 렌더링된 상품 추출
                    cur_res = await extract_dom_products(page)
                    cur_products = cur_res.get("products", [])
                    new_products = [x for x in cur_products if x.get("productTitle") not in seen_titles]

                    if not new_products:
                        logger.warning(f"[{current_page}페이지] 신규 상품 0개 -> 418 차단 또는 마지막 페이지로 판정")
                        crawl_error = f"NO_NEW_PRODUCTS_AT_PAGE_{current_page}"
                        Block418Logger.record_418(
                            keyword=keyword,
                            page=current_page,
                            attempt=1,
                            profile_name=profile_name,
                            active_workers=active_workers,
                            consecutive_success_pages=target_page,
                            request_url=page.url,
                            status_code=418,
                            extra_reason="NO_NEW_PRODUCTS"
                        )
                        break

                    start_rank = len(all_organic_products) + 1
                    end_rank = len(all_organic_products) + len(new_products)
                    logger.info(f"✔ [{current_page}페이지 수집 성공] {start_rank}위 ~ {end_rank}위 ({len(new_products)}개 신규 오가닉)")

                    for idx, item in enumerate(new_products):
                        current_rank = len(all_organic_products) + idx + 1
                        t = item.get("productTitle", "")
                        if t:
                            seen_titles.add(t)
                        match_res = DataExtractor.match_target(item, target_id)
                        if match_res:
                            matched_val, matched_field = match_res
                            target_found = True
                            target_rank = current_rank
                            target_page = current_page
                            target_product = DataExtractor.format_product_info(item, matched_val)
                            logger.info(f"★ 타겟 상품 발견: #{target_rank}위 ({target_product.get('productName')}) -> 즉시 조기 종료")
                            break

                    all_organic_products.extend(new_products)
                    target_page = current_page

                    if target_found:
                        break

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
