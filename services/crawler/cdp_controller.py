"""
services/crawler/cdp_controller.py
Chrome DevTools Protocol(CDP) 세션 초기화, 세로형(Portrait) 뷰포트/터치 주입 및 실시간 트래픽 계측 모듈
"""

import os
import json
import asyncio
from typing import Dict, Any, Optional
from playwright.async_api import Page, CDPSession
from core.logger import get_logger

logger = get_logger("crawler.cdp_controller")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEVICE_CONFIG_FILE = os.path.join(BASE_DIR, "services", "runtime", "device_config.json")


class CDPController:
    """CDP 세션 제어 및 모바일 에뮬레이션 매니저"""

    def __init__(self, page: Page):
        self.page = page
        self.cdp_session: Optional[CDPSession] = None
        self.bytes_received: int = 0
        self.device_config: Dict[str, Any] = self._load_device_config()

    def _load_device_config(self) -> Dict[str, Any]:
        if os.path.exists(DEVICE_CONFIG_FILE):
            try:
                with open(DEVICE_CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"기기 설정 로드 실패, 기본값 사용: {e}")
        return {
            "userAgent": "Mozilla/5.0 (X11; CrKey armv7l 1.54.250320) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.114 Safari/537.36",
            "screenWidth": 430,
            "screenHeight": 780,
            "deviceScaleFactor": 2.0
        }

    async def setup_session(self) -> CDPSession:
        """CDP 세션 생성 및 에뮬레이션 주입"""
        self.cdp_session = await self.page.context.new_cdp_session(self.page)
        
        # 1. 네트워크 트래픽 리스너 등록
        await self.cdp_session.send("Network.enable")
        
        def on_data_received(event):
            self._accumulate_traffic(event.get("dataLength", 0))

        def on_loading_finished(event):
            self._accumulate_traffic(event.get("encodedDataLength", 0))

        self.cdp_session.on("Network.dataReceived", on_data_received)
        self.cdp_session.on("Network.loadingFinished", on_loading_finished)

        # 2. 기기 스펙: 창 크기에 100% 핏되는 세로형 해상도 주입
        dev_w = 430
        dev_h = 780
        await self.cdp_session.send("Emulation.setDeviceMetricsOverride", {
            "width": dev_w,
            "height": dev_h,
            "deviceScaleFactor": 2,
            "mobile": True,
            "screenWidth": dev_w,
            "screenHeight": dev_h,
            "screenOrientation": {"type": "portraitPrimary", "angle": 0}
        })
        await self.cdp_session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})

        # 3. Nest Hub 고신뢰 UserAgent 주입
        await self.cdp_session.send("Emulation.setUserAgentOverride", {
            "userAgent": "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 CrKey/1.54.248666",
            "acceptLanguage": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "platform": "Android",
            "userAgentMetadata": {
                "brands": [{"brand": "Not=A?Brand", "version": "99"}, {"brand": "Google Chrome", "version": "151"}, {"brand": "Chromium", "version": "151"}],
                "fullVersion": "151.0.7922.173",
                "platform": "Android",
                "platformVersion": "10.0.0",
                "architecture": "",
                "model": "",
                "mobile": False
            }
        })

        return self.cdp_session

    def _accumulate_traffic(self, length: int):
        if length and length > 0:
            self.bytes_received += length

    @property
    def total_bytes(self) -> int:
        return self.bytes_received

    @property
    def total_kb(self) -> float:
        return round(self.bytes_received / 1024.0, 2)
