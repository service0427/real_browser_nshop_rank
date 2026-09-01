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
        하단 페이징 영역으로 스크롤 후 원본 페이징 버튼(1~25p) 정밀 클릭 수행
        """
        logger.info(f"👉 [{target_page}페이지 이동] 하단 스크롤 및 원본 페이징 버튼 정밀 클릭")

        # 1. 페이징 영역이 렌더링되도록 하단 스크롤 3~4회
        for _ in range(4):
            await page.mouse.wheel(0, 1500)
            await asyncio.sleep(0.6)

        # 2. paginator 영역 내 대상 버튼 탐색
        paginator = page.locator('div[class*="paginator_inner"], div[class*="paginator"]')
        
        if target_page <= 5:
            btn = paginator.locator('a, button').filter(has_text=str(target_page)).first
        else:
            # 6, 11, 16, 21 등 청크 시작점은 '다음' 버튼 우선 매칭, 없으면 해당 번호
            if (target_page - 1) % 5 == 0:
                btn = paginator.locator('a, button').filter(has_text="다음").first
                if await btn.count() == 0:
                    btn = paginator.locator('a, button').filter(has_text=str(target_page)).first
            else:
                btn = paginator.locator('a, button').filter(has_text=str(target_page)).first

        btn_count = await btn.count()
        if btn_count == 0:
            logger.error(f"❌ [{target_page}페이지] 페이징 버튼을 찾을 수 없음!")
            return False

        # 3. 버튼이 화면에 완전히 보이도록 스크롤 후 마우스 물리 클릭
        await btn.scroll_into_view_if_needed()
        await asyncio.sleep(0.5)
        await btn.click()
        logger.info(f"   🎯 [{target_page}페이지] 버튼 클릭 성공!")

        # 4. 데이터 로드 대기
        await asyncio.sleep(3.0)
        return True
