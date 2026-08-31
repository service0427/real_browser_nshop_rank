import json
import os
import shutil
import time
from typing import Dict, Any, List, Optional
from core.logger import get_logger

logger = get_logger("rank.profile_pool")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVED_MASTER_DIR = os.path.join(BASE_DIR, "services", "runtime", "master_profile")
POOL_DIR = os.path.join(BASE_DIR, "services", "runtime", "profiles")
COOLDOWN_SECONDS = 900  # 로그인/차단 시 15분간 숙성 쿨다운


def clean_profile_crash_state(profile_dir: str):
    """프로필 복원 팝업 제거 및 exit_type 정상화"""
    for sub in ["Default", ""]:
        pref_file = os.path.join(profile_dir, sub, "Preferences") if sub else os.path.join(profile_dir, "Preferences")
        if os.path.exists(pref_file):
            try:
                with open(pref_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "profile" in data:
                    data["profile"]["exit_type"] = "Normal"
                    data["profile"]["exited_cleanly"] = True
                with open(pref_file, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:
                pass


class ProfilePoolManager:
    """
    쓰레드/워커별 독립 프로필 풀 매니저
    - Worker 1: profile_101 ~ profile_150 (50개)
    - Worker 2: profile_201 ~ profile_250 (50개)
    - Worker N: profile_{N*100+1} ~ profile_{N*100+50} (최대 8개 쓰레드 지원)
    - 순차 롤링(Round-Robin) & 3회 연속 차단 시 자동 자가치유(Auto-Reset)
    """

    def __init__(self, worker_id: int = 1, start_id: Optional[int] = None, count: int = 50, pool_dir: str = POOL_DIR):
        self.worker_id = worker_id
        self.start_id = start_id if start_id is not None else (worker_id * 100 + 1)
        self.count = count
        self.end_id = self.start_id + self.count - 1
        self.pool_dir = pool_dir
        self.state_file = os.path.join(self.pool_dir, f"worker_{worker_id}_state.json")
        self._ensure_pool_initialized()

    def _ensure_pool_initialized(self):
        """해당 워커 범위의 50개 독립 프로필 초기 생성 및 상태 파일 생성"""
        os.makedirs(self.pool_dir, exist_ok=True)
        state = self._load_state()

        if not state or len(state.get("profiles", [])) != self.count:
            logger.info(f"📁 [Worker #{self.worker_id}] {self.count}개 독립 프로필 풀 초기화 (범위: profile_{self.start_id:03d} ~ profile_{self.end_id:03d})")
            profiles_list = []
            for i in range(self.start_id, self.end_id + 1):
                p_name = f"profile_{i:03d}"
                p_path = os.path.join(self.pool_dir, p_name)
                if not os.path.exists(p_path) and os.path.exists(SAVED_MASTER_DIR):
                    shutil.copytree(SAVED_MASTER_DIR, p_path, ignore=shutil.ignore_patterns("Singleton*", "*Lock*"))
                clean_profile_crash_state(p_path)

                profiles_list.append({
                    "id": i,
                    "name": p_name,
                    "path": p_path,
                    "status": "HEALTHY",
                    "success_count": 0,
                    "block_count": 0,
                    "consecutive_blocks": 0,
                    "last_used_at": 0,
                    "cooldown_until": 0
                })

            state = {
                "worker_id": self.worker_id,
                "start_id": self.start_id,
                "end_id": self.end_id,
                "current_index": 0,
                "total_profiles": self.count,
                "profiles": profiles_list,
                "updated_at": int(time.time())
            }
            self._save_state(state)
            logger.info(f"✔ [Worker #{self.worker_id}] {self.count}개 프로필(profile_{self.start_id:03d} ~ profile_{self.end_id:03d}) 준비 완료!")

    def _load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self, state: Dict[str, Any]):
        state["updated_at"] = int(time.time())
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"상태 파일 저장 실패: {e}")

    def acquire_next_profile(self) -> Dict[str, Any]:
        """순차 롤링(Round-Robin) 방식으로 다음 프로필 획득"""
        state = self._load_state()
        profiles = state.get("profiles", [])
        now = time.time()

        for p in profiles:
            if p.get("status") == "RESTING" and now >= p.get("cooldown_until", 0):
                p["status"] = "HEALTHY"
                p["cooldown_until"] = 0

        curr_idx = state.get("current_index", 0)
        selected_profile = profiles[curr_idx % len(profiles)]
        state["current_index"] = (curr_idx + 1) % len(profiles)
        self._save_state(state)

        clean_profile_crash_state(selected_profile["path"])
        return selected_profile

    def reset_profile(self, profile_id: int):
        """특정 프로필 세션이 손상되거나 차단 누적 시 클린 마스터 템플릿으로 완전 재설정"""
        state = self._load_state()
        profiles = state.get("profiles", [])
        for p in profiles:
            if p["id"] == profile_id:
                p_path = p["path"]
                logger.warning(f"♻️ [Worker #{self.worker_id} | Profile #{profile_id:03d}] 손상 감지 -> 프로필 폴더 리셋 및 마스터 재복제")
                try:
                    if os.path.exists(p_path):
                        shutil.rmtree(p_path, ignore_errors=True)
                    if os.path.exists(SAVED_MASTER_DIR):
                        shutil.copytree(SAVED_MASTER_DIR, p_path, ignore=shutil.ignore_patterns("Singleton*", "*Lock*"))
                    clean_profile_crash_state(p_path)
                    p["status"] = "HEALTHY"
                    p["block_count"] = 0
                    p["consecutive_blocks"] = 0
                    p["cooldown_until"] = 0
                    logger.info(f"✔ [Worker #{self.worker_id} | Profile #{profile_id:03d}] 리셋 완료 (완전 초기화)")
                except Exception as e:
                    logger.error(f"❌ [Profile #{profile_id:03d}] 리셋 실패: {e}")
                break
        self._save_state(state)

    def report_result(self, profile_id: int, success: bool, is_login_or_block: bool = False):
        """
        작업 결과 보고:
        - 성공 시: SUCCESS 카운트 증가 & 연속 차단 리셋
        - 로그인/차단 시: RESTING 쿨다운 전환 & 3회 연속 차단 시 자동 자가치유(Auto-Reset)
        """
        state = self._load_state()
        profiles = state.get("profiles", [])
        now = time.time()

        for p in profiles:
            if p["id"] == profile_id:
                p["last_used_at"] = int(now)
                if success:
                    p["success_count"] = p.get("success_count", 0) + 1
                    p["consecutive_blocks"] = 0
                    p["status"] = "HEALTHY"
                    p["cooldown_until"] = 0
                elif is_login_or_block:
                    p["block_count"] = p.get("block_count", 0) + 1
                    consec = p.get("consecutive_blocks", 0) + 1
                    p["consecutive_blocks"] = consec
                    p["status"] = "RESTING"
                    p["cooldown_until"] = int(now + COOLDOWN_SECONDS)
                    logger.warning(f"💤 [Worker #{self.worker_id} | Profile #{profile_id:03d}] 로그인/차단 감지 (연속 {consec}회) -> {COOLDOWN_SECONDS // 60}분간 자동 숙성")

                    if consec >= 3:
                        logger.warning(f"⚠️ [Worker #{self.worker_id} | Profile #{profile_id:03d}] 3회 연속 차단 발생 -> 자동 프로필 재생성(자가치유)")
                        self.reset_profile(profile_id)
                        return
                break

        self._save_state(state)


# 기본 인스턴스 (Worker 1용)
profile_pool_mgr = ProfilePoolManager(worker_id=1, count=50)
