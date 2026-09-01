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
            await more_btn.scroll_into_view_if_needed()
            await more_btn.click()
            await asyncio.sleep(3.0)
            return True

        # 대체 링크 클릭 또는 직접 이동
        catalog_link = await page.query_selector('a[href*="shopping.naver.com/search/all"]')
        if catalog_link:
            await catalog_link.click(force=True)
            await asyncio.sleep(2.0)
            return True

        logger.warning("[경고] 더보기 버튼 미발견, 쇼핑 1페이지 직접 이동")
        direct_url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}&frm=MAUIPRO"
        await page.goto(direct_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(2.0)
        return False

    @staticmethod
    async def click_next_page(page: Page, target_page: int) -> bool:
        """
        화면 정중앙(Center) 스크롤 정렬 후 실제 DOM 페이징 버튼 물리 마우스 클릭 수행 (1~25페이지 지원)
        """
        logger.info(f"👉 [{target_page}페이지 이동] DOM 페이징 버튼 탐색 및 화면 중앙 정렬 물리 클릭")

        # 1. 페이징 영역이 렌더링되도록 부드러운 휠 스크롤 3회
        for _ in range(3):
            await page.mouse.wheel(0, 1200)
            await asyncio.sleep(0.6)

        # 2. 버튼 탐색 및 화면 중앙(block: center)으로 스크롤하여 정확한 물리 좌표 추출
        btn_info = await page.evaluate("""(target) => {
            const paginator = document.querySelector('div[class*="paginator"]');
            if (!paginator) return { ok: false, reason: 'no_paginator' };

            const btns = Array.from(paginator.querySelectorAll('a, button'));
            let btn = btns.find(b => b.innerText.trim() === String(target));

            // 6, 11, 16, 21페이지 등 청크 이동 시 '다음리스트' 또는 '다음' 버튼 매칭
            if (!btn && (target % 5 === 1 || target > 5)) {
                btn = btns.find(b => b.innerText.trim().includes('다음') || b.getAttribute('aria-label')?.includes('다음'));
            }

            if (!btn) {
                return { ok: false, reason: 'btn_not_found', available: btns.map(b => b.innerText.trim()) };
            }

            // 버튼을 뷰포트 정중앙으로 스크롤 정렬
            btn.scrollIntoView({ behavior: 'instant', block: 'center' });
            const rect = btn.getBoundingClientRect();
            return {
                ok: true,
                text: btn.innerText.trim(),
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
                visible: rect.top >= 0 && rect.top <= window.innerHeight
            };
        }""", target_page)

        if not btn_info or not btn_info.get("ok"):
            logger.error(f"❌ [{target_page}페이지] 버튼 탐색 실패: {btn_info}")
            return False

        logger.info(f"   🎯 [{target_page}p 타겟] '{btn_info['text']}' 화면 정중앙 좌표 ({btn_info['x']:.1f}, {btn_info['y']:.1f}) -> 리얼 물리 클릭!")

        # 3. 마우스 물리 이동 및 클릭 이벤트 발생
        await page.mouse.move(btn_info["x"], btn_info["y"], steps=8)
        await asyncio.sleep(0.15)
        await page.mouse.down()
        await asyncio.sleep(0.1)
        await page.mouse.up()

        # 4. 데이터 로드 대기
        await asyncio.sleep(3.0)
        return True
