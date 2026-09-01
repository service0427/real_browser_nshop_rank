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
    """Chrome 브라우저 프로세스 실행 및 생명주기 관리자"""

    @classmethod
    def launch(
        cls,
        port: int,
        profile_path: str,
        headless: bool = False,
        disk_cache_dir: Optional[str] = None
    ) -> subprocess.Popen:
        """Chrome 브라우저 프로세스를 실행하고 Popen 인스턴스 반환"""
        cache_dir = disk_cache_dir or SHARED_CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)

        user_dir = profile_path or os.path.join(BASE_DIR, "services", "runtime", "master_profile")
        os.makedirs(user_dir, exist_ok=True)

        # 이전 충돌이나 비정상 종료로 남은 Singleton 락 파일 청소
        for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            lock_path = os.path.join(user_dir, lock_name)
            if os.path.islink(lock_path) or os.path.exists(lock_path):
                try:
                    os.unlink(lock_path)
                except Exception:
                    pass

        chrome_cmd = [
            "/usr/bin/google-chrome",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_dir}",
            f"--disk-cache-dir={cache_dir}",
            "--disk-cache-size=1073741824",  # 1GB 캐시
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-dev-shm-usage",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-infobars",
            "--disable-blink-features=AutomationControlled",
            "--test-type"
        ]

        if headless:
            chrome_cmd.append("--headless=new")

        # GUI 데스크톱 환경변수 명시적 주입 (Wayland / X11 / XAUTHORITY)
        chrome_env = os.environ.copy()
        uid = os.getuid()
        if "DISPLAY" not in chrome_env or not chrome_env["DISPLAY"]:
            chrome_env["DISPLAY"] = ":0"
        if "WAYLAND_DISPLAY" not in chrome_env or not chrome_env["WAYLAND_DISPLAY"]:
            chrome_env["WAYLAND_DISPLAY"] = "wayland-0"
        if "XDG_RUNTIME_DIR" not in chrome_env or not chrome_env["XDG_RUNTIME_DIR"]:
            chrome_env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

        # Mutter/Xwayland 권한 파일 자동 감지 및 주입
        if "XAUTHORITY" not in chrome_env or not chrome_env["XAUTHORITY"]:
            import glob
            mutter_auths = glob.glob(f"/run/user/{uid}/.mutter-Xwaylandauth.*")
            if mutter_auths:
                chrome_env["XAUTHORITY"] = mutter_auths[0]
            elif os.path.exists(os.path.expanduser("~/.Xauthority")):
                chrome_env["XAUTHORITY"] = os.path.expanduser("~/.Xauthority")

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
