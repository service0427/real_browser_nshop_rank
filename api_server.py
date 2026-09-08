"""
api_server.py
Naver Organic Ranking Unified FastAPI Server (Stage 2 PC 고속 스캔 & Stage 3 폰팜 딥 스캔 지원)
"""

import os
import sys
import asyncio
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Query, Body, HTTPException
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.logger import get_logger
from services.crawler import crawl_shopping_rank_async
from services.phone_farm import phone_device_mgr, crawl_phone_rank_async

logger = get_logger("api.server")

app = FastAPI(
    title="TechB Naver Organic Rank API",
    description="Stage 2 (PC Fast 1~200위) 및 Stage 3 (Real Phone Farm 1~1000위) 통합 순위 수집 API",
    version="3.0.0"
)


class RankRequest(BaseModel):
    keyword: str = Field(..., description="검색 키워드 (예: 무선이어폰)")
    target: Optional[str] = Field(None, description="타겟 상품 ID (nvMid, channelProductId 등)")
    target_id: Optional[str] = Field(None, description="타겟 상품 ID (target과 동일)")
    stage: int = Field(2, description="수집 단계 (2: PC 1~200위 고속, 3: 폰팜 1~1000위 딥 스캔)")
    max_pages: Optional[int] = Field(None, description="최대 수집 페이지 (기본값: Stage 2는 5p, Stage 3는 25p)")
    headless: bool = Field(False, description="헤드리스 모드 (기본값: False - Real GUI)")
    include_raw_products: bool = Field(False, description="수집된 전수 상품 리스트 포함 여부 (Stage 3 전용)")


@app.get("/")
def root():
    return {
        "service": "TechB Naver Rank API",
        "version": "3.0.0",
        "supported_stages": {
            "stage_2": "PC Real Browser Cluster (1~5 pages / 1~200 ranks, ~5s)",
            "stage_3": "Real Phone Farm Cluster (1~25 pages / 1~1000 ranks + 229 raw metadata, ~40s)"
        }
    }


@app.get("/api/health")
def health():
    devices = phone_device_mgr.scan_devices()
    return {
        "status": "HEALTHY",
        "connected_phones_count": len(devices),
        "connected_phones": devices
    }


@app.get("/api/devices")
def get_devices():
    """폰팜에 연결된 실기기 목록 및 상태 반환"""
    return {
        "total_count": len(phone_device_mgr.devices),
        "devices": phone_device_mgr.scan_devices()
    }


@app.post("/api/rank")
async def get_rank(req: RankRequest):
    """
    [통합 엔드포인트] stage=2 또는 stage=3 파라미터로 PC/폰팜 자동 분기
    """
    target = req.target or req.target_id

    # Stage 2: PC Real GUI Browser (1~200위 고속)
    if req.stage == 2:
        max_p = req.max_pages or 5
        logger.info(f"⚡ [API Stage 2 | PC] 키워드: '{req.keyword}', 타겟: '{target}', max_pages: {max_p}")
        res = await crawl_shopping_rank_async(
            keyword=req.keyword,
            target_id=target or "",
            max_pages=max_p,
            headless=req.headless
        )
        res["stage"] = 2
        return res

    # Stage 3: Real Phone Farm (1~1000위 딥 스캔 + 200 OK JSON)
    elif req.stage == 3:
        max_p = req.max_pages or 25
        logger.info(f"📱 [API Stage 3 | 폰팜] 키워드: '{req.keyword}', 타겟: '{target}', max_pages: {max_p}")
        res = await crawl_phone_rank_async(
            keyword=req.keyword,
            target_id=target,
            max_pages=max_p,
            include_raw_products=req.include_raw_products
        )
        return res

    else:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 stage 값입니다 ({req.stage}). 2 또는 3을 입력해주세요.")


@app.post("/api/rank/fast")
async def get_rank_fast(req: RankRequest):
    """
    [2단계 전용 엔드포인트] PC 고속 스캔 (1~5페이지 / 1~200위)
    """
    req.stage = 2
    return await get_rank(req)


@app.post("/api/rank/deep")
async def get_rank_deep(req: RankRequest):
    """
    [3단계 전용 엔드포인트] 폰팜 딥 스캔 (1~25페이지 / 1~1,000위 + 풀 메타데이터)
    """
    req.stage = 3
    return await get_rank(req)
