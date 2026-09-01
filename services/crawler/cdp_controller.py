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
        """CDP 세션 생성 및 모바일 에뮬레이션 주입"""
        self.cdp_session = await self.page.context.new_cdp_session(self.page)
        
        # 1. 네트워크 트래픽 리스너 등록
        await self.cdp_session.send("Network.enable")
        
        def on_data_received(event):
            self._accumulate_traffic(event.get("dataLength", 0))

        def on_loading_finished(event):
            self._accumulate_traffic(event.get("encodedDataLength", 0))

        self.cdp_session.on("Network.dataReceived", on_data_received)
        self.cdp_session.on("Network.loadingFinished", on_loading_finished)

        # 2. Chrome DevTools Nest Hub 정밀 에뮬레이션 주입 (1024x600, Scale 2, Landscape)
        vp = self.device_config.get("viewport", {})
        dev_w = vp.get("innerWidth", 1024)
        dev_h = vp.get("innerHeight", 600)
        dpr = vp.get("devicePixelRatio", 2)
        
        await self.cdp_session.send("Emulation.setDeviceMetricsOverride", {
            "width": dev_w,
            "height": dev_h,
            "deviceScaleFactor": dpr,
            "mobile": True,
            "screenWidth": dev_w,
            "screenHeight": dev_h,
            "screenOrientation": {"type": "landscapePrimary", "angle": 0}
        })
        await self.cdp_session.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})

        # 3. Nest Hub 고신뢰 UserAgent & Client Hints 주입
        nav = self.device_config.get("navigator", {})
        ua = nav.get("userAgent", "Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.7977.64 Safari/537.36 CrKey/1.54.248666")
        ch = self.device_config.get("clientHints", {})

        await self.cdp_session.send("Emulation.setUserAgentOverride", {
            "userAgent": ua,
            "acceptLanguage": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "platform": "Linux armv8l",
            "userAgentMetadata": {
                "brands": ch.get("brands", [
                    {"brand": "Not?A_Brand", "version": "24"},
                    {"brand": "Google Chrome", "version": "152"},
                    {"brand": "Chromium", "version": "152"}
                ]),
                "fullVersionList": ch.get("fullVersionList", [
                    {"brand": "Not?A_Brand", "version": "24.0.0.0"},
                    {"brand": "Google Chrome", "version": "152.0.7977.64"},
                    {"brand": "Chromium", "version": "152.0.7977.64"}
                ]),
                "fullVersion": "152.0.7977.64",
                "platform": "Android",
                "platformVersion": "10",
                "architecture": "",
                "model": "Nest Hub",
                "mobile": True
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
