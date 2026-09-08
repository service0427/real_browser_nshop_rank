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
        사용자 원본 검증 로직:
        하단에 있는 실제 네이버 페이징 DOM 버튼을 화면 상단(Fixed)에 그대로 복제(cloneNode)하여
        상단에서 마우스 물리 클릭을 수행하는 고속 페이징 처리
        """
        logger.info(f"👉 [{target_page}페이지 이동] 실제 네이버 페이징 DOM 복제(Clone) 및 상단 리얼 클릭")

        # 페이징 버튼 탐색 후 즉시 상단(Fixed)에 원본 DOM 복제(cloneNode) - 노스크롤 고속 페이징
        clone_js = f"""
        (() => {{
            const buttons = Array.from(document.querySelectorAll('a[role="button"], button, a, [class*="paginator"] a, [class*="paginator"] button'));
            let targetBtn = buttons.find(el => el.textContent.trim() === '{target_page}' && !el.getAttribute('data-is-clone'));
            if (!targetBtn && ({target_page} - 1) % 5 === 0) {{
                targetBtn = buttons.find(el => {{
                    const txt = el.textContent.trim();
                    const aria = el.getAttribute('aria-label') || '';
                    const cls = el.className || '';
                    return (txt === '다음' || txt.includes('다음') || aria.includes('다음') || cls.includes('next') || cls.includes('btn_next'));
                }});
            }}
            if (!targetBtn) return {{ success: false, reason: 'not_found' }};

            // 기존 복제 엘리먼트 제거
            document.querySelectorAll('[data-is-clone="true"]').forEach(el => el.remove());

            // 대상 버튼 DOM 완전 복제
            const clone = targetBtn.cloneNode(true);
            clone.setAttribute('data-is-clone', 'true');
            clone.style.position = 'fixed';
            clone.style.top = '120px';
            clone.style.left = '30px';
            clone.style.zIndex = '999999';
            clone.style.opacity = '1';
            clone.style.pointerEvents = 'auto';
            clone.style.backgroundColor = '#FFFF00';
            clone.style.outline = '4px solid #FF0055';
            clone.style.boxShadow = '0 0 25px rgba(255, 0, 85, 1.0)';
            clone.style.transform = 'scale(1.25)';
            clone.style.padding = '8px 16px';
            clone.style.borderRadius = '8px';
            clone.style.color = '#000000';
            clone.style.fontWeight = '900';

            // 클릭 이벤트 위임
            clone.onclick = (e) => {{
                e.preventDefault();
                e.stopPropagation();
                targetBtn.click();
            }};

            document.body.appendChild(clone);
            const r = clone.getBoundingClientRect();
            return {{
                success: true,
                text: targetBtn.textContent.trim(),
                x: r.left + r.width / 2,
                y: r.top + r.height / 2
            }};
        }})()
        """
        res = await page.evaluate(clone_js)

        if not res or not res.get("success"):
            # 차선책: 상단 플로팅 방식 폴백
            logger.warning(f"[{target_page}p 복제 실패 -> 상단 플로팅 폴백 시도]")
            fallback_res = await page.evaluate("""(target) => {
                const p = document.querySelector('div[class*="paginator_inner"], div[class*="paginator"]');
                if (!p) return null;
                p.style.position = 'fixed';
                p.style.top = '80px';
                p.style.left = '20px';
                p.style.zIndex = '999999';
                p.style.background = '#ffffff';
                p.style.border = '3px solid #00c73c';
                const btns = Array.from(p.querySelectorAll('a, button'));
                let targetBtn = btns.find(el => el.innerText.trim() === String(target));
                if (!targetBtn) {
                    targetBtn = btns.find(el => el.innerText.trim().includes('다음') || el.getAttribute('aria-label')?.includes('다음'));
                }
                if (!targetBtn) return null;
                const r = targetBtn.getBoundingClientRect();
                return { text: targetBtn.innerText.trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 };
            }""", target_page)
            if not fallback_res:
                logger.error(f"❌ [{target_page}페이지] 페이징 버튼 탐색 실패!")
                return False
            res = {"success": True, "text": fallback_res["text"], "x": fallback_res["x"], "y": fallback_res["y"]}

        logger.info(f"✨ [{target_page}p 복제 버튼: '{res.get('text')}'] 상단 배치 완료 (좌표: {res['x']:.1f}, {res['y']:.1f})")

        # 3. 시각적 확인 0.5초 대기 후 마우스 물리 이동 및 리얼 클릭
        await asyncio.sleep(0.5)
        await page.mouse.move(res["x"], res["y"], steps=10)
        await asyncio.sleep(0.15)
        await page.mouse.down()
        await asyncio.sleep(0.1)
        await page.mouse.up()

        # 4. 복제 엘리먼트 제거
        await page.evaluate("() => { document.querySelectorAll('[data-is-clone=\"true\"]').forEach(el => el.remove()); }")

        # 5. 데이터 로드 대기
        await asyncio.sleep(3.0)
        return True

