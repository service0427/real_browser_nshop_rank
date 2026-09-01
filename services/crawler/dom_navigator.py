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

    @classmethod
    async def click_next_page(cls, page: Page, target_page: int) -> bool:
        """
        하단 페이징 영역으로 스크롤 후 대상 버튼을 시각적으로 강조(Highlight)하고
        사용자가 눈으로 확인할 수 있도록 대기 후 리얼 물리 마우스 클릭 수행
        """
        logger.info(f"👉 [{target_page}페이지 이동] 하단 탐색 및 시각적 하이라이트 후 리얼 클릭")

        paginator = page.locator('div[class*="paginator_inner"], div[class*="paginator"]')

        # 1. 대상 버튼이 DOM에 나타날 때까지 대기 및 부드러운 스크롤
        btn = None
        for _ in range(12):
            if (target_page - 1) % 5 == 0:
                btn = paginator.locator('a, button').filter(has_text=str(target_page)).first
                if await btn.count() == 0:
                    btn = paginator.locator('button:has-text("다음리스트"), a:has-text("다음")').first
            else:
                btn = paginator.locator('a, button').filter(has_text=str(target_page)).first

            if await btn.count() > 0:
                break

            # 휠 스크롤 수행
            mx = random.randint(350, 650)
            my = random.randint(200, 450)
            await page.mouse.move(mx, my, steps=2)
            await page.mouse.wheel(0, random.randint(400, 750))
            await asyncio.sleep(random.uniform(0.1, 0.2))

        if not btn or await btn.count() == 0:
            logger.error(f"❌ [{target_page}페이지] 페이징 버튼을 찾을 수 없음!")
            return False

        # 2. 버튼이 화면에 완전히 보이도록 스크롤
        await btn.scroll_into_view_if_needed()
        await asyncio.sleep(0.4)

        # 3. 시각적 하이라이트 효과 부여 (형광 노랑 배경 + 네온 레드 테두리 + 그림자 + 확대)
        await btn.evaluate("""el => {
            el.dataset.prevStyle = el.getAttribute('style') || '';
            el.style.outline = '5px solid #FF0055';
            el.style.backgroundColor = '#FFFF00';
            el.style.color = '#000000';
            el.style.fontWeight = '900';
            el.style.boxShadow = '0 0 35px rgba(255, 0, 85, 1.0)';
            el.style.transform = 'scale(1.3)';
            el.style.transition = 'all 0.3s ease';
            el.style.zIndex = '99999';
        }""")

        btn_text = await btn.inner_text()
        box = await btn.bounding_box()
        if not box:
            logger.error(f"❌ [{target_page}페이지] 버튼 좌표 계산 실패!")
            return False

        logger.info(f"✨ [{target_page}p 버튼: '{btn_text}'] 시각적 하이라이트 표시 중! (좌표: {box['x']:.1f}, {box['y']:.1f})")

        # 4. 사용자가 눈으로 확인할 수 있도록 1.2초간 대기
        await asyncio.sleep(1.2)

        # 5. 마우스 커서를 버튼 중앙으로 자연스럽게 이동 후 리얼 물리 클릭
        target_x = box["x"] + box["width"] / 2
        target_y = box["y"] + box["height"] / 2
        logger.info(f"👉 [{target_page}p 버튼] 마우스 물리 이동 ({target_x:.1f}, {target_y:.1f}) -> 리얼 물리 클릭!")

        await page.mouse.move(target_x, target_y, steps=16)
        await asyncio.sleep(0.2)
        await page.mouse.down()
        await asyncio.sleep(0.12)
        await page.mouse.up()

        # 6. 하이라이트 스타일 원복
        await btn.evaluate("""el => {
            el.style.transform = 'scale(1.0)';
            el.style.outline = 'none';
            el.style.boxShadow = 'none';
        }""")

        # 7. 데이터 로드 대기
        await asyncio.sleep(4.0)
        return True
