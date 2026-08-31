"""
services/crawler/data_extractor.py
네이버 쇼핑 NEXT_DATA JSON 데이터 파싱, 오가닉/광고 분리 및 타겟 상품 100% ID 매칭 모듈
"""

import json
import re
from typing import Dict, Any, List, Optional, Tuple
from playwright.async_api import Page
from core.logger import get_logger

logger = get_logger("crawler.data_extractor")


class DataExtractor:
    """네이버 쇼핑 데이터 추출 및 랭크 매칭 엔진"""

    @staticmethod
    async def extract_next_data(page: Page) -> Optional[Dict[str, Any]]:
        """페이지 내 __NEXT_DATA__ 스크립트 태그 JSON 추출"""
        try:
            script_content = await page.evaluate("""() => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }""")
            if script_content:
                return json.loads(script_content)
        except Exception as e:
            logger.debug(f"NEXT_DATA JSON 파싱 실패: {e}")
        return None

    @classmethod
    def parse_products_from_next_data(cls, next_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
        """
        NEXT_DATA 트리에서 compositeProducts 추출 및 오가닉 상품 목록과 광고 개수 반환
        반환값: (organic_products, ad_count)
        """
        organic_products: List[Dict[str, Any]] = []
        ad_count = 0

        try:
            props = next_data.get("props", {}).get("pageProps", {})
            
            raw_products = []
            if isinstance(props.get("compositeProducts"), dict):
                raw_products = props.get("compositeProducts", {}).get("list", [])
            elif isinstance(props.get("compositeProducts"), list):
                raw_products = props.get("compositeProducts", [])
            
            if not raw_products and isinstance(props.get("products"), dict):
                raw_products = props.get("products", {}).get("list", [])
            elif not raw_products and isinstance(props.get("products"), list):
                raw_products = props.get("products", [])

            if not raw_products and isinstance(props.get("initialState"), dict):
                init_products = props.get("initialState", {}).get("products", {})
                if isinstance(init_products, dict):
                    raw_products = init_products.get("list", []) or init_products.get("compositeProducts", [])

            if isinstance(raw_products, list):
                for item in raw_products:
                    if not isinstance(item, dict):
                        continue
                    item_dict = item.get("item", item)
                    if not isinstance(item_dict, dict):
                        continue
                    
                    # 광고 상품 필터링
                    is_ad = bool(
                        item.get("isAd") is True
                        or item.get("ad") is True
                        or item_dict.get("isAd") is True
                        or item_dict.get("ad") is True
                        or item_dict.get("adContent")
                        or item_dict.get("adId")
                    )
                    if is_ad:
                        ad_count += 1
                        continue

                    organic_products.append(item_dict)
        except Exception as e:
            logger.error(f"상품 목록 파싱 중 오류: {e}")

        return organic_products, ad_count

    @staticmethod
    def match_target(product_item: Dict[str, Any], target_id: str) -> Optional[Tuple[str, str]]:
        """
        타겟 식별자(nvMid, channelProductId, ID)와 1:1 완벽 일치 여부 검사
        반환값: (matched_id, matched_field_name) 또는 None
        """
        target_clean = str(target_id).strip()
        
        # URL 형태 타겟인 경우 ID 추출
        if "products/" in target_clean:
            match = re.search(r"products/(\d+)", target_clean)
            if match:
                target_clean = match.group(1)

        candidate_fields = [
            ("id", product_item.get("id")),
            ("nvMid", product_item.get("nvMid")),
            ("channelProductId", product_item.get("channelProductId")),
            ("channelServiceId", product_item.get("channelServiceId")),
            ("mallProductId", product_item.get("mallProductId")),
            ("productId", product_item.get("productId")),
        ]

        for field_name, val in candidate_fields:
            if val is not None and str(val).strip() == target_clean:
                return str(val).strip(), field_name

        return None

    @staticmethod
    def format_product_info(item: Dict[str, Any], matched_val: str) -> Dict[str, Any]:
        """서버 반환용 표준 상품 메타데이터 포맷 생성"""
        raw_price = item.get("lowPrice") or item.get("price") or 0
        try:
            low_price = int(str(raw_price).replace(",", ""))
        except Exception:
            low_price = 0

        mall_info = item.get("mallInfoCache", {}) or {}
        mall_name = mall_info.get("name") or item.get("mallName") or ""
        mall_count = item.get("mallCount", 0)
        if mall_count and int(mall_count) > 1:
            mall_name = f"가격비교 (몰 {mall_count}개)"

        return {
            "productName": item.get("productTitle") or item.get("name") or item.get("title") or "",
            "mallName": mall_name,
            "lowPrice": low_price,
            "imageUrl": item.get("imageUrl") or item.get("image") or "",
            "reviewCount": int(item.get("reviewCount", 0) or 0),
            "scoreInfo": float(item.get("scoreInfo", 0.0) or 0.0),
            "nvMid": str(item.get("nvMid") or item.get("id") or matched_val),
            "brand": item.get("brand") or "",
            "category": f"{item.get('category1Name', '')}>{item.get('category2Name', '')}>{item.get('category3Name', '')}>{item.get('category4Name', '')}".strip(">")
        }
