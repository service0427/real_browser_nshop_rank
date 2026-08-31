import datetime
import json
import os
import re
import time
from typing import Dict, Any, Optional, List
from core.logger import get_logger

logger = get_logger("rank.keyword_cache")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORD_CACHE_DIR = os.path.join(BASE_DIR, "services", "runtime", "keyword_cache")
LEGACY_CACHE_FILE = os.path.join(BASE_DIR, "services", "runtime", "saved_keyword_ranks.json")


def _get_safe_filename(keyword: str) -> str:
    """특수문자 및 공백을 파일명 안전 포맷으로 치환"""
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', keyword.strip())
    return f"{safe_name}.json"


class KeywordRankCacheManager:
    """
    키워드 단위 독립 파일 캐시 매니저
    - 경로: saved_keyword_cache/{키워드}.json
    - 1페이지(40개) ~ 10페이지(400개) 수집된 상품/순위 데이터를 키워드별 개별 JSON 파일로 저장
    - 동일 키워드 조회 시 0.0002초 만에 개별 파일/메모리에서 타겟 매칭 (트래픽 0KB)
    - 네이버 쇼핑 갱신 주기(11:00, 19:00) 기준 스마트 TTL 관리
    """

    def __init__(self, cache_dir: str = KEYWORD_CACHE_DIR):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._memory_cache: Dict[str, Any] = {}
        self.load_all_caches()

    def count(self) -> int:
        return len(self._memory_cache)

    def load_all_caches(self):
        """디렉토리 내 모든 키워드 캐시 파일 로드 및 레거시 파일 마이그레이션"""
        count = 0
        
        # 1. 개별 키워드 파일 로드
        for fname in os.listdir(self.cache_dir):
            if fname.endswith(".json"):
                fpath = os.path.join(self.cache_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        kw = data.get("keyword")
                        if kw:
                            self._memory_cache[kw] = data
                            count += 1
                except Exception as e:
                    logger.error(f"❌ [KeywordCache] '{fname}' 로드 실패: {e}")

        # 2. 레거시 단일 파일 마이그레이션
        if os.path.exists(LEGACY_CACHE_FILE):
            try:
                with open(LEGACY_CACHE_FILE, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                    for kw, data in legacy_data.items():
                        if kw not in self._memory_cache:
                            self._memory_cache[kw] = data
                            self._save_single_keyword_file(kw, data)
                            count += 1
                os.remove(LEGACY_CACHE_FILE)
            except Exception:
                pass

        logger.info(f"💾 [KeywordCache] {count}개 키워드 개별 캐시 로드 완료 (위치: {self.cache_dir})")

    def _save_single_keyword_file(self, keyword: str, data: Dict[str, Any]):
        """단일 키워드 데이터를 독립 파일로 디스크 저장"""
        safe_fname = _get_safe_filename(keyword)
        target_path = os.path.join(self.cache_dir, safe_fname)
        temp_path = f"{target_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, target_path)
        except Exception as e:
            logger.error(f"❌ [KeywordCache] '{safe_fname}' 저장 실패: {e}")

    def _is_cache_fresh(self, cached_timestamp: float) -> bool:
        """네이버 쇼핑 갱신 주기(11:00, 19:00) 기반 신선도 검사"""
        now = time.time()
        age = now - cached_timestamp
        if age > 21600:  # 6시간 초과 시 만료
            return False

        now_dt = datetime.datetime.fromtimestamp(now)
        cached_dt = datetime.datetime.fromtimestamp(cached_timestamp)

        if now_dt.date() == cached_dt.date():
            if cached_dt.hour < 11 and now_dt.hour >= 11:
                return False
            if cached_dt.hour < 19 and now_dt.hour >= 19:
                return False
        else:
            if now_dt.hour >= 11 or cached_dt.hour < 19:
                return False

        return True

    def lookup(self, keyword: str, target_id: Optional[str]) -> Dict[str, Any]:
        """키워드 캐시 조회"""
        keyword_clean = keyword.strip()
        target_clean = str(target_id).strip() if target_id else None

        # 메모리에 없으면 디스크 파일에서 직접 읽기 시도
        if keyword_clean not in self._memory_cache:
            safe_fname = _get_safe_filename(keyword_clean)
            target_path = os.path.join(self.cache_dir, safe_fname)
            if os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        self._memory_cache[keyword_clean] = json.load(f)
                except Exception:
                    return {"hit": False, "reason": "READ_ERROR"}
            else:
                return {"hit": False, "reason": "NO_CACHE"}

        kw_data = self._memory_cache[keyword_clean]
        cached_at = kw_data.get("cached_at", 0)

        if not self._is_cache_fresh(cached_at):
            return {"hit": False, "reason": "CACHE_EXPIRED"}

        id_map = kw_data.get("id_map", {})
        max_page = kw_data.get("max_page", 1)

        # 1. 타겟 매칭 확인 (CACHE HIT)
        if target_clean and target_clean in id_map:
            matched_info = id_map[target_clean]
            logger.info(f"⚡ [캐시 적중 (CACHE HIT)] 키워드='{keyword_clean}', 타겟='{target_clean}' -> #{matched_info['rank']}위 [{matched_info.get('product', {}).get('productName', '')}]")
            return {
                "hit": True,
                "found": True,
                "rank": matched_info["rank"],
                "product": matched_info.get("product"),
                "matchedField": matched_info.get("matchedField", "item.id"),
                "maxPage": max_page,
                "cachedAt": cached_at,
                "cacheFile": os.path.join(self.cache_dir, _get_safe_filename(keyword_clean)),
                "source": "CACHE_MATCH"
            }

        # 2. 10페이지(400위) 전수 조사 완료 키워드인 경우 -> 0위 즉시 확정
        if max_page >= 10:
            logger.info(f"⚡ [캐시 0위 확정 (CACHE 400 EXHAUSTED)] 키워드='{keyword_clean}', 타겟='{target_clean}' -> 400위 내 없음 (0위 즉시 반환)")
            return {
                "hit": True,
                "found": False,
                "rank": 0,
                "product": None,
                "matchedField": None,
                "maxPage": max_page,
                "cachedAt": cached_at,
                "cacheFile": os.path.join(self.cache_dir, _get_safe_filename(keyword_clean)),
                "source": "CACHE_EXHAUSTED_400"
            }

        # 3. 추가 탐색 필요
        return {
            "hit": False,
            "reason": "PARTIAL_CACHE",
            "cachedMaxPage": max_page,
            "cachedCount": len(kw_data.get("products", []))
        }

    def update(self, keyword: str, products: List[Dict[str, Any]], max_page_crawled: int):
        """키워드별 개별 JSON 파일 갱신/저장"""
        keyword_clean = keyword.strip()
        now = time.time()

        existing = self._memory_cache.get(keyword_clean, {})
        existing_products = existing.get("products", []) if self._is_cache_fresh(existing.get("cached_at", 0)) else []
        existing_id_map = existing.get("id_map", {}) if self._is_cache_fresh(existing.get("cached_at", 0)) else {}

        seen_ranks = set()
        merged_products = []
        for p in (products + existing_products):
            r = p.get("rank")
            if r and r not in seen_ranks:
                seen_ranks.add(r)
                merged_products.append(p)

        merged_products.sort(key=lambda x: x.get("rank", 9999))

        id_map = existing_id_map.copy()
        for item in merged_products:
            r = item.get("rank")
            prod_clean = {k: v for k, v in item.items() if k != "rawItem"}
            
            for id_key in ["id", "nvMid", "channelProductId"]:
                val = str(item.get(id_key) or "").strip()
                if val:
                    id_map[val] = {"rank": r, "product": prod_clean, "matchedField": f"item.{id_key}"}

            raw_data = item.get("rawItem", {})
            if isinstance(raw_data, dict):
                for k in ["parentId", "stdGroupId", "channelProductId", "originalMallProductId"]:
                    val = str(raw_data.get(k) or "").strip()
                    if val and val not in id_map:
                        id_map[val] = {"rank": r, "product": prod_clean, "matchedField": f"item.{k}"}

        max_page = max(max_page_crawled, existing.get("max_page", 1))

        payload = {
            "keyword": keyword_clean,
            "cached_at": now,
            "cached_at_str": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "max_page": max_page,
            "total_items": len(merged_products),
            "id_map": id_map,
            "products": merged_products
        }

        self._memory_cache[keyword_clean] = payload
        self._save_single_keyword_file(keyword_clean, payload)
        
        safe_fname = _get_safe_filename(keyword_clean)
        logger.info(f"💾 [KeywordCache] '{safe_fname}' 파일 저장 완료 (총 {len(merged_products)}개 상품, {len(id_map)}개 식별자, {max_page}페이지)")

    save = update

    def purge(self, keyword: str):
        """남은 상품이 1개(keyword_remaining_count=1)인 마지막 조회 완료 후 캐시 파일 및 메모리 즉시 삭제"""
        keyword_clean = keyword.strip()
        if keyword_clean in self._memory_cache:
            del self._memory_cache[keyword_clean]
        safe_fname = _get_safe_filename(keyword_clean)
        target_path = os.path.join(self.cache_dir, safe_fname)
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
                logger.info(f"🧹 [KeywordCache] '{keyword_clean}' 마지막 상품(remaining=1) 처리 완료 -> 캐시 즉시 정리(Purge)")
            except Exception as e:
                logger.warning(f"⚠️ [KeywordCache] '{keyword_clean}' 캐시 파일 삭제 실패: {e}")


keyword_cache_mgr = KeywordRankCacheManager()
