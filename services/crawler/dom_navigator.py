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
            page1_url = await more_btn.get_attribute("href")
            if page1_url:
                await page.evaluate(f"window.location.href = '{page1_url}'")
            else:
                await more_btn.scroll_into_view_if_needed()
                await more_btn.click(force=True)
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
        실제 네이버 페이징 DOM 버튼 복제(Clone) 및 상단 물리 클릭 수행 (1~25페이지 / 최대 1000위 지원)
        """
        logger.info(f"👉 [{target_page}페이지 이동] 실제 네이버 페이징 DOM 버튼 복제(Clone) 및 상단 리얼 클릭 수행")

        # 페이징 영역이 렌더링되도록 하단 스크롤
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
        except Exception:
            pass
        
        clone_js = f"""
        (() => {{
            const buttons = Array.from(document.querySelectorAll('a[role="button"], button, a, [class*="paginator"], [class*="pagination"]'));
            // 1. 목표 페이지 번호 버튼 직접 검색
            let targetBtn = buttons.find(el => el.textContent.trim() === '{target_page}' && !el.getAttribute('data-is-clone'));
            
            // 2. 만약 해당 페이지 번호가 없으면 '다음' 버튼 검색 (10p->11p, 20p->21p 등 페이징 블록 전환)
            if (!targetBtn) {{
                targetBtn = buttons.find(el => {{
                    const txt = el.textContent.trim();
                    const aria = el.getAttribute('aria-label') || '';
                    const cls = el.className || '';
                    const title = el.getAttribute('title') || '';
                    return (txt === '다음' || txt === '다음페이지' || aria.includes('다음') || title.includes('다음') || cls.includes('btn_next') || cls.includes('next')) && !el.getAttribute('data-is-clone');
                }});
            }}

            if (!targetBtn) return {{ success: false, reason: 'not_found' }};

            const clone = targetBtn.cloneNode(true);
            clone.setAttribute('data-is-clone', 'true');
            clone.style.position = 'fixed';
            clone.style.top = '150px';
            clone.style.left = '20px';
            clone.style.zIndex = '999999';
            clone.style.opacity = '1';
            clone.style.pointerEvents = 'auto';
            document.body.appendChild(clone);
            return {{ success: true }};
        }})()
        """
        
        res = await page.evaluate(clone_js)
        if not res.get("success"):
            # 차선책: DOM 직접 클릭
            fallback_js = f"""
            (() => {{
                const buttons = Array.from(document.querySelectorAll('a[role="button"], button, a, [class*="paginator"], [class*="pagination"]'));
                let targetBtn = buttons.find(el => el.textContent.trim() === '{target_page}');
                if (!targetBtn) {{
                    targetBtn = buttons.find(el => {{
                        const txt = el.textContent.trim();
                        const aria = el.getAttribute('aria-label') || '';
                        const cls = el.className || '';
                        const title = el.getAttribute('title') || '';
                        return (txt === '다음' || txt === '다음페이지' || aria.includes('다음') || title.includes('다음') || cls.includes('btn_next') || cls.includes('next'));
                    }});
                }}
                if (targetBtn) {{
                    targetBtn.click();
                    return true;
                }}
                return false;
            }})()
            """
            success = await page.evaluate(fallback_js)
            await asyncio.sleep(2.0)
            return success

        logger.info("   ✔ 복제 상태: [네이버 실제 DOM 원형 복제] -> 마우스 물리 클릭")
        await asyncio.sleep(0.3)
        await page.click('a[data-is-clone="true"], button[data-is-clone="true"], [data-is-clone="true"]', force=True)
        
        # 복제 엘리먼트 정리
        await page.evaluate("() => { document.querySelectorAll('[data-is-clone=\"true\"]').forEach(el => el.remove()); }")
        await asyncio.sleep(2.0)
        return True
