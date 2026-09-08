"""
services/block_logger.py
네이버 WTM 418 차단 발생 히스토리 일자별 전용 로깅 모듈
- 페이지 깊이별 vs 동시 워커 사용량별 차단 원인 분석용
- 일자별 텍스트 로그 (.log) 및 분석용 구조화 데이터 (.jsonl) 동시 저장
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional
from core.logger import get_logger

logger = get_logger("block_logger")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "services", "runtime", "logs")


class Block418Logger:
    """418 차단 히스토리 일자별 로거"""

    @classmethod
    def get_log_paths(cls) -> tuple[str, str]:
        """오늘 날짜 기준 로그 파일 경로 반환 (txt_path, jsonl_path)"""
        os.makedirs(LOGS_DIR, exist_ok=True)
        today_str = datetime.now().strftime("%Y-%m-%d")
        txt_path = os.path.join(LOGS_DIR, f"418_history_{today_str}.log")
        jsonl_path = os.path.join(LOGS_DIR, f"418_history_{today_str}.jsonl")
        return txt_path, jsonl_path

    @classmethod
    def record_418(
        cls,
        keyword: str,
        page: int,
        attempt: int = 1,
        profile_name: str = "default",
        active_workers: int = 1,
        consecutive_success_pages: int = 0,
        request_url: str = "",
        status_code: int = 418,
        extra_reason: str = "WTM_RATE_LIMIT"
    ):
        """
        418 차단 발생 시 상세 컨텍스트를 일자별 파일로 기록
        """
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")

        event_data: Dict[str, Any] = {
            "timestamp": timestamp_str,
            "epoch": time.time(),
            "status_code": status_code,
            "keyword": keyword,
            "page": page,
            "attempt": attempt,
            "profile_name": profile_name,
            "active_workers": active_workers,
            "consecutive_success_pages": consecutive_success_pages,
            "request_url": request_url,
            "reason": extra_reason
        }

        # 1. 텍스트 로그 라인 포맷
        log_line = (
            f"[{timestamp_str}] [418_BLOCKED] "
            f"키워드: '{keyword}' | 발생페이지: {page}p (연속성공: {consecutive_success_pages}p) | "
            f"재시도: {attempt}/3 | 프로필: {profile_name} | 동시실행워커: {active_workers}개 | "
            f"사유: {extra_reason} | URL: {request_url}\n"
        )

        txt_path, jsonl_path = cls.get_log_paths()

        try:
            # 텍스트 로그 저장
            with open(txt_path, "a", encoding="utf-8") as f:
                f.write(log_line)

            # 구조화된 JSONL 저장
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_data, ensure_ascii=False) + "\n")

            logger.warning(
                f"🚨 [418 History Logged] '{keyword}' {page}페이지 차단 기록 완료 "
                f"(동시워커: {active_workers}개, 연속성공: {consecutive_success_pages}p) -> {txt_path}"
            )
        except Exception as e:
            logger.error(f"[Block418Logger] 파일 저장 실패: {e}")

    @classmethod
    def record_abnormal(
        cls,
        event_type: str,
        keyword: str,
        target_id: str = "",
        page: int = 1,
        worker: str = "mobile",
        device_or_profile: str = "",
        error_message: str = "",
        elapsed_sec: float = 0.0
    ):
        """
        418 차단, 타임아웃, 페이징 실패 등 모든 비정상 상태를 전용 로그 파일에 일자별로 영구 누적
        """
        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        os.makedirs(LOGS_DIR, exist_ok=True)
        txt_path = os.path.join(LOGS_DIR, f"abnormal_history_{today_str}.log")
        jsonl_path = os.path.join(LOGS_DIR, f"abnormal_history_{today_str}.jsonl")

        event_data: Dict[str, Any] = {
            "timestamp": timestamp_str,
            "epoch": time.time(),
            "event_type": event_type,
            "worker": worker,
            "keyword": keyword,
            "target_id": target_id,
            "page": page,
            "device_or_profile": device_or_profile,
            "error_message": error_message,
            "elapsed_sec": elapsed_sec
        }

        log_line = (
            f"[{timestamp_str}] [{event_type}] "
            f"워커: {worker} | 기기/프로필: {device_or_profile} | 키워드: '{keyword}' | 타겟: '{target_id}' | "
            f"발생페이지: {page}p | 사유: {error_message} | 소요: {elapsed_sec}s\n"
        )

        try:
            with open(txt_path, "a", encoding="utf-8") as f:
                f.write(log_line)
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_data, ensure_ascii=False) + "\n")

            logger.warning(
                f"🚨 [비정상 상태 기록] {event_type} | '{keyword}' ({page}p, {worker}/{device_or_profile}) "
                f"-> 패스(Requeue) 처리 및 로그 기록 완료: {txt_path}"
            )
        except Exception as e:
            logger.error(f"[Block418Logger] 비정상 로그 파일 저장 실패: {e}")

