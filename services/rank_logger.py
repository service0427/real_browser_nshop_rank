"""
services/rank_logger.py
키워드별 순위/상품 목록 텍스트 파일 저장 모듈
형식: 순위 | MID | 제목
"""

import os
import re
from typing import List, Dict, Any
from core.logger import get_logger

logger = get_logger("rank_logger")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RANK_LOGS_DIR = os.path.join(BASE_DIR, "services", "runtime", "rank_logs")


class RankLogger:
    """키워드별 1~1000위 상품 목록 라인별 텍스트 파일 저장기"""

    @classmethod
    def save_keyword_ranks(cls, keyword: str, products: List[Dict[str, Any]]) -> str:
        """
        수집된 오가닉 상품 목록을 '순위 | MID | 제목' 형식의 텍스트 파일로 저장
        파일명: services/runtime/rank_logs/{keyword}_ranks.txt
        """
        try:
            os.makedirs(RANK_LOGS_DIR, exist_ok=True)
            # 파일명에 안전하지 않은 특수문자 제거
            safe_kw = re.sub(r'[\\/*?:"<>|]', '_', keyword.strip())
            file_path = os.path.join(RANK_LOGS_DIR, f"{safe_kw}_ranks.txt")

            lines = []
            for idx, p in enumerate(products, 1):
                mid = str(p.get("id") or p.get("nvMid") or p.get("channelProductId") or "").strip()
                title = str(p.get("productTitle") or p.get("productName") or p.get("title") or "").strip()
                # 줄바꿈 및 파이프 문자 정제
                title_clean = title.replace("\n", " ").replace("\r", "").replace("|", "-")
                lines.append(f"{idx} | {mid} | {title_clean}")

            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))

            logger.info(f"💾 [RankLogger] '{keyword}' 키워드 순위 리스트 저장 완료 -> {file_path} (총 {len(lines)}개 상품)")
            return file_path
        except Exception as e:
            logger.error(f"[RankLogger] 파일 저장 실패 ({keyword}): {e}")
            return ""
