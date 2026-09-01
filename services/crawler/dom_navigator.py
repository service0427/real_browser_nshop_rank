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
        스크롤 지연을 완전히 제거하기 위해 DOM 내 존재하는 원본 페이징 엘리먼트를
        클래스명/속성 변경 없이 화면 상단(Fixed)에 띄우고,
        시각적 하이라이트 후 리얼 물리 마우스 클릭을 수행하여 고속 페이징 처리
        """
        logger.info(f"👉 [{target_page}페이지 이동] 노-스크롤 상단 플로팅 및 리얼 마우스 클릭")

        # 1. 원본 페이징 엘리먼트를 상단에 고정 플로팅하고 대상 버튼 탐색 및 하이라이트
        btn_info = await page.evaluate("""(target) => {
            const p = document.querySelector('div[class*="paginator_inner"], div[class*="paginator"]');
            if (!p) return null;

            // 원본 엘리먼트를 화면 최상단 눈에 보이는 위치에 고정 (클래스명 등은 100% 유지)
            p.style.position = 'fixed';
            p.style.top = '80px';
            p.style.left = '20px';
            p.style.zIndex = '999999';
            p.style.background = '#ffffff';
            p.style.padding = '12px 20px';
            p.style.borderRadius = '12px';
            p.style.boxShadow = '0 8px 35px rgba(0,0,0,0.6)';
            p.style.border = '3px solid #00c73c';

            // 대상 버튼 탐색
            const btns = Array.from(p.querySelectorAll('a, button'));
            let targetBtn = null;

            if ((target - 1) % 5 === 0) {
                targetBtn = btns.find(el => el.innerText.trim() === String(target));
                if (!targetBtn) {
                    targetBtn = btns.find(el => el.innerText.trim().includes('다음') || el.getAttribute('aria-label')?.includes('다음'));
                }
            } else {
                targetBtn = btns.find(el => el.innerText.trim() === String(target));
            }

            if (!targetBtn) return null;

            // 대상 버튼 시각적 하이라이트 효과 적용 (형광 노랑 + 네온 레드 테두리 + 확대)
            targetBtn.style.outline = '4px solid #FF0055';
            targetBtn.style.backgroundColor = '#FFFF00';
            targetBtn.style.color = '#000000';
            targetBtn.style.fontWeight = '900';
            targetBtn.style.boxShadow = '0 0 25px rgba(255, 0, 85, 1.0)';
            targetBtn.style.transform = 'scale(1.25)';
            targetBtn.style.transition = 'all 0.3s ease';

            const r = targetBtn.getBoundingClientRect();
            return {
                text: targetBtn.innerText.trim(),
                x: r.left + r.width / 2,
                y: r.top + r.height / 2
            };
        }""", target_page)

        if not btn_info:
            logger.error(f"❌ [{target_page}페이지] 페이징 버튼 탐색 실패!")
            return False

        logger.info(f"✨ [{target_page}p 버튼: '{btn_info['text']}'] 상단 고정 플로팅 완료 (좌표: {btn_info['x']:.1f}, {btn_info['y']:.1f})")

        # 2. 사용자가 눈으로 확인할 수 있도록 0.8초 대기
        await asyncio.sleep(0.8)

        # 3. 마우스 커서를 상단 고정 버튼으로 물리 이동 후 리얼 클릭
        logger.info(f"👉 [{target_page}p 버튼] 마우스 물리 이동 ({btn_info['x']:.1f}, {btn_info['y']:.1f}) -> 리얼 물리 클릭!")
        await page.mouse.move(btn_info["x"], btn_info["y"], steps=14)
        await asyncio.sleep(0.18)
        await page.mouse.down()
        await asyncio.sleep(0.12)
        await page.mouse.up()

        # 4. 데이터 로드 대기 (스크롤이 필요 없으므로 3.5초 만에 신속 완료)
        await asyncio.sleep(3.5)
        return True
