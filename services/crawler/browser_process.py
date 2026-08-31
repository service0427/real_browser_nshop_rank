"""
services/crawler/browser_process.py
Chrome 브라우저 프로세스 생명주기 관리, 8분할 그리드 창 배치 및 리눅스 GUI 환경 자동 바인딩 모듈
"""

import os
import subprocess
import asyncio
from typing import Optional, Dict, Any, Tuple
from core.logger import get_logger

logger = get_logger("crawler.browser_process")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHARED_CACHE_DIR = os.path.join(BASE_DIR, "services", "runtime", "browser_cache")


class BrowserProcessManager:
    """Chrome 브라우저 프로세스 실행 및 윈도우 배치 관리자"""

    @staticmethod
    def calculate_window_layout(port: int, max_threads: int = 8) -> Tuple[int, int, int, int]:
        """
        우분투 데스크톱 GUI 환경(좌측 시작표시줄 Dock ~75px, 상단 패널 ~35px)에 맞춘 4x2 그리드 타일링 배치.
        1920x1080 Full HD 및 다양한 해상도에서 8개 창이 서로 겹치지 않고 정렬되도록 계산.
        반환값: (win_w, win_h, win_x, win_y)
        """
        # 우분투 좌측 독(Dock/시작표시줄) 및 상단 패널 오프셋 마진
        dock_offset_x = 75   # 좌측 시작표시줄 여백
        panel_offset_y = 35  # 상단 패널 여백
        
        # 4열 x 2행 배치 규격 (스크롤바 및 브라우저 프레임 여유폭 포함)
        win_w = 450
        win_h = 500
        gap_x = 10
        gap_y = 12

        # 9201 -> worker_idx 0, 9202 -> worker_idx 1 ...
        worker_idx = max(0, port - 9201) if port >= 9201 else 0

        col = worker_idx % 4  # 0, 1, 2, 3 (4열)
        row = worker_idx // 4 # 0, 1       (2행)

        win_x = dock_offset_x + (col * (win_w + gap_x))
        win_y = panel_offset_y + (row * (win_h + gap_y))

        return win_w, win_h, win_x, win_y

    @classmethod
    def launch(
        cls,
        port: int,
        profile_path: str,
        headless: bool = False,
        disk_cache_dir: Optional[str] = None
    ) -> subprocess.Popen:
        """Chrome 브라우저 프로세스를 실행하고 Popen 인스턴스 반환"""
        win_w, win_h, win_x, win_y = cls.calculate_window_layout(port)
        cache_dir = disk_cache_dir or SHARED_CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)

        user_dir = profile_path or os.path.join(BASE_DIR, "services", "runtime", "master_profile")

        chrome_cmd = [
            "/usr/bin/google-chrome",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_dir}",
            f"--disk-cache-dir={cache_dir}",
            "--disk-cache-size=1073741824",  # 1GB 캐시
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-infobars",
            f"--window-size={win_w},{win_h}",
            f"--window-position={win_x},{win_y}"
        ]

        if headless:
            chrome_cmd.append("--headless=new")

        # GUI 데스크톱 환경변수 명시적 주입 (Wayland / X11)
        chrome_env = os.environ.copy()
        if "DISPLAY" not in chrome_env or not chrome_env["DISPLAY"]:
            chrome_env["DISPLAY"] = ":0"
        if "WAYLAND_DISPLAY" not in chrome_env or not chrome_env["WAYLAND_DISPLAY"]:
            chrome_env["WAYLAND_DISPLAY"] = "wayland-0"
        if "XDG_RUNTIME_DIR" not in chrome_env or not chrome_env["XDG_RUNTIME_DIR"]:
            chrome_env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"

        proc = subprocess.Popen(
            chrome_cmd,
            env=chrome_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return proc

    @staticmethod
    async def terminate(proc: Optional[subprocess.Popen], port: int):
        """Chrome 프로세스 및 포트 점유 안전 종료"""
        if proc:
            try:
                proc.terminate()
                await asyncio.sleep(0.3)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass

        # 포트 잔여 프로세스 정리
        try:
            cleanup_cmd = f"fuser -k {port}/tcp 2>/dev/null || true"
            subprocess.run(cleanup_cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
