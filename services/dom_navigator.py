"""
services/crawler/dom_navigator.py
네이버 모바일 통합검색 진입, 공식 '가격비교 더보기' 버튼 물리 클릭 및 실제 DOM 복제 페이징 모듈
"""

import random
import string
import urllib.parse
import asyncio
from typing import Optional, Dict, Any
from playwright.async_api import Page
from core.logger import get_logger

logger = get_logger("crawler.dom_navigator")


class DOMNavigator:
    """네이버 쇼핑 DOM 네비게이션 및 페이징 제어 매니저"""

    @staticmethod
    def generate_ackey(length: int = 8) -> str:
        """네이버 모바일 통합검색 진입용 8자리 랜덤 ackey 생성"""
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

    @classmethod
    async def navigate_to_search(cls, page: Page, keyword: str) -> bool:
        """모바일 통합검색 진입 -> 가격비교 더보기 버튼 발견 및 클릭"""
        encoded_query = urllib.parse.quote(keyword)
        ackey = cls.generate_ackey()
        search_url = f"https://m.search.naver.com/search.naver?sm=mtp_hty.top&where=m&query={encoded_query}&ackey={ackey}"
        
        logger.info(f"[1] 모바일 통합검색 진입: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(2.0)

        # '가격비교 더보기' 버튼 탐색 및 리얼 클릭
        more_btn = await page.query_selector("a.x3mTJJja:has-text('네이버 가격비교 더보기'), a:has-text('네이버 가격비교 더보기'), a[role='button']:has(span.text:has-text('가격비교 더보기')), a:has(span:has-text('가격비교 더보기'))")
        if more_btn:
            logger.info("[2] 공식 '가격비교 더보기' 버튼 발견 -> 리얼 클릭 수행")
            try:
                await more_btn.scroll_into_view_if_needed(timeout=2500)
            except Exception:
                pass
            try:
                await more_btn.click(timeout=3000)
            except Exception:
                try:
                    await more_btn.evaluate("el => el.click()")
                except Exception:
                    pass
            await asyncio.sleep(3.0)

            # [로그인 창 방지 및 자동 우회 핸들러]
            if "nidlogin" in page.url or "nid.naver.com" in page.url:
                logger.warning("🚨 [PC Stage 2] 네이버 로그인 창(nidlogin) 감지 -> 쇼핑 검색(msearch) 직접 우회 진입")
                direct_url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}"
                await page.goto(direct_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2.0)
            return True

        # 대체 링크 클릭 또는 직접 이동
        catalog_link = await page.query_selector('a[href*="shopping.naver.com/search/all"]')
        if catalog_link:
            try:
                await catalog_link.click(force=True, timeout=3000)
            except Exception:
                try:
                    await catalog_link.evaluate("el => el.click()")
                except Exception:
                    pass
            await asyncio.sleep(2.0)
            if "nidlogin" in page.url or "nid.naver.com" in page.url:
                logger.warning("🚨 [PC Stage 2] 네이버 로그인 창(nidlogin) 감지 -> 쇼핑 검색(msearch) 직접 우회 진입")
                direct_url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}"
                await page.goto(direct_url, wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2.0)
            return True

        logger.warning("[경고] 더보기 버튼 미발견, 쇼핑 1페이지 직접 이동")
        direct_url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}"
        await page.goto(direct_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(2.0)
        return False

    @classmethod
    async def click_next_page(cls, page: Page, target_page: int) -> bool:
        """
        Zero-Scroll 원칙 완벽 준수:
        하단에 렌더링되어 있는 네이버 페이징 DOM을 화면 상단(fixed, top: 120px)으로 플로팅 배치한 뒤
        물리 마우스(CDP page.mouse)로 실제 버튼을 직접 클릭 (isTrusted: true).
        합성 이벤트(synthetic targetBtn.click)로 인한 WTM 418 차단을 원천 방지하고 200 OK를 보장.
        """
        logger.info(f"👉 [{target_page}페이지 이동] Zero-Scroll 상단 플로팅 & 하드웨어 리얼 클릭")

        locate_js = f"""
        (() => {{
            const p = document.querySelector('div[class*="paginator_list_paging"], div[class*="paginator_inner"], div[class*="paginator"]');
            if (!p) return {{ success: false, reason: 'no_paginator' }};

            // 1. 페이징 영역을 스크롤 없이 화면 상단(fixed)에 배치
            p.style.position = 'fixed';
            p.style.top = '120px';
            p.style.left = '10px';
            p.style.zIndex = '999999';
            p.style.backgroundColor = '#ffffff';
            p.style.padding = '8px 12px';
            p.style.borderRadius = '8px';
            p.style.boxShadow = '0 4px 20px rgba(0,0,0,0.3)';

            const buttons = Array.from(p.querySelectorAll('a, button'));
            
            // 2. 목표 페이지 번호 버튼 탐색
            let targetBtn = buttons.find(el => el.textContent.trim() === '{target_page}');

            // 3. 다음 블록 이동이 필요한 경우 ('다음리스트' 또는 '다음' 버튼)
            if (!targetBtn) {{
                targetBtn = buttons.find(el => {{
                    const txt = el.textContent.trim();
                    const aria = el.getAttribute('aria-label') || '';
                    const cls = el.className || '';
                    return (txt === '다음리스트' || txt === '다음' || txt.includes('다음') || aria.includes('다음') || cls.includes('next') || cls.includes('btn_next'));
                }});
            }}

            if (!targetBtn) return {{ success: false, reason: 'button_not_found' }};

            const r = targetBtn.getBoundingClientRect();
            return {{
                success: true,
                text: targetBtn.textContent.trim(),
                x: r.left + r.width / 2,
                y: r.top + r.height / 2
            }};
        }})()
        """
        res = await page.evaluate(locate_js)

        if not res or not res.get("success"):
            logger.warning(f"❌ [{target_page}페이지] 페이징 버튼 탐색 실패: {res.get('reason') if res else 'unknown'}")
            # 복구: 스타일 초기화
            await page.evaluate("""() => {
                const p = document.querySelector('div[class*="paginator_list_paging"], div[class*="paginator_inner"], div[class*="paginator"]');
                if (p) p.style.position = '';
            }""")
            return False

        logger.info(f"✨ [{target_page}p 대상 버튼: '{res.get('text')}'] 상단 배치 완료 (좌표: {res['x']:.1f}, {res['y']:.1f})")

        # 4. 마우스 물리 이동 및 리얼 하드웨어 클릭 (isTrusted = true)
        await asyncio.sleep(0.3)
        await page.mouse.move(res["x"], res["y"], steps=6)
        await asyncio.sleep(0.15)
        await page.mouse.down()
        await asyncio.sleep(0.1)
        await page.mouse.up()

        # 5. 클릭 후 플로팅 스타일 해제하여 원래 DOM 복원
        await asyncio.sleep(0.5)
        await page.evaluate("""() => {
            const p = document.querySelector('div[class*="paginator_list_paging"], div[class*="paginator_inner"], div[class*="paginator"]');
            if (p) p.style.position = '';
        }""")

        # 6. 네트워크 데이터 로드 대기
        await asyncio.sleep(2.5)
        return True


