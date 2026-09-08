import datetime
import json
import os
import re
import shutil
import time
from typing import Dict, Any, Optional, List, Tuple
from core.logger import get_logger

logger = get_logger("rank.keyword_cache")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORD_CACHE_DIR = os.path.join(BASE_DIR, "services", "runtime", "keyword_cache")
LEGACY_CACHE_FILE = os.path.join(BASE_DIR, "services", "runtime", "saved_keyword_ranks.json")
DEFAULT_RETENTION_DAYS = 30


def _get_safe_filename(keyword: str) -> str:
    """특수문자 및 공백을 파일명 안전 포맷으로 치환"""
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', keyword.strip())
    return f"{safe_name}.json"


def get_session_info(timestamp: Optional[float] = None) -> Tuple[str, str]:
    """
    타임스탬프(기본값: 현재시각) 기준 네이버 쇼핑 갱신 세션 정보 반환
    - 11:00:00 ~ 18:59:59 -> (YYYY-MM-DD, "11")
    - 19:00:00 ~ 23:59:59 -> (YYYY-MM-DD, "19")
    - 00:00:00 ~ 10:59:59 -> (어제날짜, "19") (전날 19시 세션의 연속)
    반환값: (date_str, session_str) 예: ("2026-09-06", "11")
    """
    ts = timestamp if timestamp is not None else time.time()
    dt = datetime.datetime.fromtimestamp(ts)
    if 11 <= dt.hour < 19:
        date_str = dt.strftime("%Y-%m-%d")
        session_str = "11"
    elif dt.hour >= 19:
        date_str = dt.strftime("%Y-%m-%d")
        session_str = "19"
    else:
        yesterday = dt - datetime.timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")
        session_str = "19"
    return date_str, session_str


class KeywordRankCacheManager:
    """
    네이버 쇼핑 하루 2회 세션(11시/19시) & 30일 히스토리 아카이빙 캐시 매니저
    - 경로: services/runtime/keyword_cache/{YYYY-MM-DD}/{session}/{keyword}.json
    - 세션 윈도우 내에서는 단일/다수 슬롯 무관하게 100% 전수 캐시 우선 조회 (0.0001초 반환)
    - 수집된 __NEXT_DATA__ 전수 상품은 세션 폴더에 통으로 영구 보관 (purge 삭제 폐지)
    - 30일 경과 디렉토리는 auto_prune()으로 자동 롤링 정리
    """

    def __init__(self, cache_dir: str = KEYWORD_CACHE_DIR, retention_days: int = DEFAULT_RETENTION_DAYS):
        self.cache_dir = cache_dir
        self.retention_days = retention_days
        os.makedirs(self.cache_dir, exist_ok=True)
        self._memory_cache: Dict[str, Any] = {}
        
        cur_date, cur_sess = get_session_info()
        self._current_session_key: str = f"{cur_date}_{cur_sess}"
        self._last_prune_time: float = time.time()

        self.auto_prune(self.retention_days)
        self.migrate_and_load_active_session()

    def count(self) -> int:
        return len(self._memory_cache)

    def get_session_dir(self, date_str: str, session_str: str) -> str:
        """세션 디렉토리 반환 및 자동 생성"""
        sdir = os.path.join(self.cache_dir, date_str, session_str)
        os.makedirs(sdir, exist_ok=True)
        return sdir

    def _create_symlink(self, real_path: str, link_path: str):
        """[심볼릭 링크 미사용] 루트에는 날짜 디렉토리만 유지하므로 no-op 처리"""
        pass

    def _check_session_rollover(self):
        """11:00 또는 19:00 경과 시 메모리 캐시 자동 롤오버 및 세션 디렉토리 전환"""
        cur_date, cur_sess = get_session_info()
        new_session_key = f"{cur_date}_{cur_sess}"
        if new_session_key != self._current_session_key:
            logger.info(f"🔄 [KeywordCache] 세션 전환 감지: {self._current_session_key} ➜ {new_session_key}")
            self._current_session_key = new_session_key
            self._memory_cache.clear()
            self.migrate_and_load_active_session()

        now = time.time()
        if now - self._last_prune_time > 3600:
            self._last_prune_time = now
            self.auto_prune(self.retention_days)

    def migrate_and_load_active_session(self):
        """기존 플랫 파일 자동 마이그레이션 및 현재 세션 캐시 메모리 로드"""
        cur_date, cur_sess = get_session_info()
        self._current_session_key = f"{cur_date}_{cur_sess}"
        active_dir = self.get_session_dir(cur_date, cur_sess)

        # 1. 루트의 일반 플랫 .json 파일들을 수집 시각(cached_at 또는 mtime)에 맞춰 세션 폴더로 이동
        try:
            for item in os.listdir(self.cache_dir):
                item_path = os.path.join(self.cache_dir, item)
                if os.path.isfile(item_path) and not os.path.islink(item_path) and item.endswith(".json") and not item.endswith(".tmp"):
                    try:
                        c_at = None
                        try:
                            with open(item_path, "r", encoding="utf-8") as f:
                                j_data = json.load(f)
                                c_at = j_data.get("cached_at")
                        except Exception:
                            pass

                        if c_at is None:
                            c_at = os.path.getmtime(item_path)

                        f_date, f_sess = get_session_info(c_at)
                        target_dir = self.get_session_dir(f_date, f_sess)
                        target_file = os.path.join(target_dir, item)

                        if not os.path.exists(target_file):
                            shutil.move(item_path, target_file)
                        else:
                            os.remove(item_path)

                    except Exception as e:
                        logger.error(f"❌ [KeywordCache] 파일 마이그레이션 실패 ({item}): {e}")
        except Exception as e:
            logger.error(f"❌ [KeywordCache] 마이그레이션 디렉토리 탐색 오류: {e}")

        # 2. 레거시 단일 파일 마이그레이션
        if os.path.exists(LEGACY_CACHE_FILE):
            try:
                with open(LEGACY_CACHE_FILE, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)
                    for kw, data in legacy_data.items():
                        c_at = data.get("cached_at", time.time())
                        f_date, f_sess = get_session_info(c_at)
                        target_dir = self.get_session_dir(f_date, f_sess)
                        target_file = os.path.join(target_dir, _get_safe_filename(kw))
                        if not os.path.exists(target_file):
                            with open(target_file, "w", encoding="utf-8") as out_f:
                                json.dump(data, out_f, ensure_ascii=False, indent=2)
                os.remove(LEGACY_CACHE_FILE)
            except Exception:
                pass

        # 3. 현재 활성 세션 디렉토리의 모든 파일 메모리 로드
        count = 0
        if os.path.exists(active_dir):
            for fname in os.listdir(active_dir):
                if fname.endswith(".json") and not fname.endswith(".tmp"):
                    fpath = os.path.join(active_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            kw = data.get("keyword")
                            if kw:
                                data["_session_key"] = self._current_session_key
                                self._memory_cache[kw] = data
                                count += 1
                    except Exception as e:
                        logger.error(f"❌ [KeywordCache] '{fname}' 로드 실패: {e}")

        logger.info(f"💾 [KeywordCache] 활성 세션({cur_date}/{cur_sess}) {count}개 키워드 캐시 로드 완료 (위치: {active_dir})")

    def auto_prune(self, retention_days: int = DEFAULT_RETENTION_DAYS):
        """30일 경과된 과거 날짜 디렉토리 자동 롤링 정리 및 깨진 심볼릭 링크 정리"""
        try:
            today = datetime.date.today()
            # 1. 30일 지난 날짜 디렉토리 삭제
            for dname in os.listdir(self.cache_dir):
                dpath = os.path.join(self.cache_dir, dname)
                if os.path.isdir(dpath) and len(dname) == 10 and dname.count("-") == 2:
                    try:
                        dir_date = datetime.date.fromisoformat(dname)
                        diff_days = (today - dir_date).days
                        if diff_days > retention_days:
                            shutil.rmtree(dpath)
                            logger.info(f"🧹 [KeywordCache] {retention_days}일 경과 만료 캐시 자동 정리: {dname} ({diff_days}일 경과)")
                    except Exception:
                        pass

            # 2. 루트 디렉토리의 모든 심볼릭 링크 정리 (루트에는 YYYY-MM-DD 디렉토리만 유지)
            for fname in os.listdir(self.cache_dir):
                fpath = os.path.join(self.cache_dir, fname)
                if os.path.islink(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"❌ [KeywordCache] auto_prune 실행 오류: {e}")

    def _is_cache_fresh(self, cached_timestamp: float) -> bool:
        """현재 세션과 동일한 세션에 수집된 캐시인지 신선도 검사 (11시/19시 자동 분기)"""
        now = time.time()
        cur_date, cur_sess = get_session_info(now)
        cached_date, cached_sess = get_session_info(cached_timestamp)
        return (cur_date == cached_date) and (cur_sess == cached_sess) and (now - cached_timestamp <= 86400)

    def lookup(self, keyword: str, target_id: Optional[str]) -> Dict[str, Any]:
        """
        키워드 캐시 조회 (0.0001초 응답)
        - 활성 세션 메모리 및 세션 디렉토리 우선 조회
        """
        self._check_session_rollover()

        keyword_clean = keyword.strip()
        target_clean = str(target_id).strip() if target_id else None

        cur_date, cur_sess = get_session_info()
        cur_session_key = f"{cur_date}_{cur_sess}"

        # 1. 메모리 캐시 확인
        kw_data = self._memory_cache.get(keyword_clean)
        if not kw_data or kw_data.get("_session_key") != cur_session_key or not self._is_cache_fresh(kw_data.get("cached_at", 0)):
            safe_fname = _get_safe_filename(keyword_clean)
            active_dir = self.get_session_dir(cur_date, cur_sess)
            target_path = os.path.join(active_dir, safe_fname)

            if os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        disk_data = json.load(f)
                    if self._is_cache_fresh(disk_data.get("cached_at", 0)):
                        disk_data["_session_key"] = cur_session_key
                        self._memory_cache[keyword_clean] = disk_data
                        kw_data = disk_data
                    else:
                        kw_data = None
                except Exception:
                    kw_data = None
            else:
                kw_data = None

        if not kw_data:
            return {"hit": False, "reason": "NO_CACHE"}

        cached_at = kw_data.get("cached_at", 0)
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
                "cacheFile": os.path.join(self.get_session_dir(cur_date, cur_sess), _get_safe_filename(keyword_clean)),
                "source": "CACHE_MATCH"
            }

        # 2. 25페이지(1000위) 전수 조사 완료 키워드인 경우 -> 0위 즉시 확정
        if max_page >= 25:
            logger.info(f"⚡ [캐시 0위 확정 (CACHE 1000 EXHAUSTED)] 키워드='{keyword_clean}', 타겟='{target_clean}' -> 1000위 내 없음 (0위 즉시 반환)")
            return {
                "hit": True,
                "found": False,
                "rank": 0,
                "product": None,
                "matchedField": None,
                "maxPage": max_page,
                "cachedAt": cached_at,
                "cacheFile": os.path.join(self.get_session_dir(cur_date, cur_sess), _get_safe_filename(keyword_clean)),
                "source": "CACHE_EXHAUSTED_1000"
            }

        # 3. 추가 탐색 필요
        return {
            "hit": False,
            "reason": "PARTIAL_CACHE",
            "cachedMaxPage": max_page,
            "cachedCount": len(kw_data.get("products", []))
        }

    def update(self, keyword: str, products: List[Dict[str, Any]], max_page_crawled: int):
        """
        키워드별 세션 폴더에 개별 JSON 파일 갱신/저장 (무조건 통으로 저장)
        - 수집된 오가닉 상품 전수를 누적 병합
        - atomic write로 프로세스 충돌 방지
        - 루트 심볼릭 링크 생성으로 타 프로세스(Stage 3 등) 즉각 공유
        """
        self._check_session_rollover()

        keyword_clean = keyword.strip()
        now = time.time()
        cur_date, cur_sess = get_session_info(now)
        cur_session_key = f"{cur_date}_{cur_sess}"

        existing = self._memory_cache.get(keyword_clean, {})
        if not existing or existing.get("_session_key") != cur_session_key:
            active_dir = self.get_session_dir(cur_date, cur_sess)
            fpath = os.path.join(active_dir, _get_safe_filename(keyword_clean))
            if os.path.exists(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}

        existing_is_fresh = self._is_cache_fresh(existing.get("cached_at", 0))
        existing_products = existing.get("products", []) if existing_is_fresh else []
        existing_id_map = existing.get("id_map", {}) if existing_is_fresh else {}

        # 랭크 기준 병합 (새 수집 결과 우선, 중복 랭크 제거)
        seen_ranks = set()
        merged_products = []
        for p in products:
            r = p.get("rank")
            if r and r not in seen_ranks:
                seen_ranks.add(r)
                merged_products.append(p)

        for p in existing_products:
            r = p.get("rank")
            if r and r not in seen_ranks:
                seen_ranks.add(r)
                merged_products.append(p)

        merged_products.sort(key=lambda x: x.get("rank", 9999))

        id_map = existing_id_map.copy() if existing_is_fresh else {}
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

        max_page = max(max_page_crawled, existing.get("max_page", 1) if existing_is_fresh else 1)

        payload = {
            "keyword": keyword_clean,
            "cached_at": now,
            "cached_at_str": datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "session_date": cur_date,
            "session_hour": cur_sess,
            "_session_key": cur_session_key,
            "max_page": max_page,
            "total_items": len(merged_products),
            "id_map": id_map,
            "products": merged_products
        }

        self._memory_cache[keyword_clean] = payload
        self._save_single_keyword_file(keyword_clean, payload, cur_date, cur_sess)

        safe_fname = _get_safe_filename(keyword_clean)
        logger.info(f"💾 [KeywordCache] '{cur_date}/{cur_sess}/{safe_fname}' 파일 저장 완료 (총 {len(merged_products)}개 상품, {len(id_map)}개 식별자, {max_page}페이지)")

    def _save_single_keyword_file(self, keyword: str, data: Dict[str, Any], date_str: str, session_str: str):
        """단일 키워드 데이터를 세션 디렉토리에 원자적(Atomic)으로 디스크 저장"""
        target_dir = self.get_session_dir(date_str, session_str)
        safe_fname = _get_safe_filename(keyword)
        target_path = os.path.join(target_dir, safe_fname)
        temp_path = f"{target_path}.tmp_{os.getpid()}_{int(time.time() * 1000)}"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, target_path)
        except Exception as e:
            logger.error(f"❌ [KeywordCache] '{safe_fname}' 저장 실패: {e}")

    save = update

    def purge(self, keyword: str):
        """
        [30일 히스토리 보존 정책]
        캐시 파일은 30일간 히스토리 보존되므로 디스크에서 삭제하지 않습니다.
        (레거시 호출 호환성 유지용 no-op)
        """
        pass


keyword_cache_mgr = KeywordRankCacheManager()
