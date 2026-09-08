"""
services/phone_farm/web_viewer.py
20대 폰팜 실시간 웹 모니터링 & 웹 원격 제어 대시보드 (Port 8080)
PC 브라우저(크롬 등)에서 접속하여 20대 화면을 한눈에 보고 마우스 클릭/터치 조작 가능
"""

import os
import io
import time
import asyncio
import subprocess
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import uvicorn

app = FastAPI(title="Phone Farm Web Live Viewer")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 최신 프레임 캐시
frame_cache: Dict[str, bytes] = {}
device_info_cache: Dict[str, Dict[str, Any]] = {}


def get_connected_devices() -> List[str]:
    """현재 ADB 연결된 기기 시리얼/IP 목록 반환"""
    res = subprocess.run(["adb", "devices"], capture_output=True, text=True)
    devices = []
    for line in res.stdout.strip().splitlines()[1:]:
        if "\tdevice" in line:
            devices.append(line.split()[0])
    return sorted(devices)


def capture_phone_frame(serial: str) -> Optional[bytes]:
    """ADB screencap으로 화면 캡처 후 웹 최적화 JPEG(360x800)로 압축"""
    try:
        cmd = ["adb", "-s", serial, "exec-out", "screencap", "-p"]
        p = subprocess.run(cmd, capture_output=True, timeout=1.5)
        if p.returncode == 0 and len(p.stdout) > 1000:
            img = Image.open(io.BytesIO(p.stdout))
            # 가로 360px 비율 유지 리사이즈 (웹 대역폭 최적화)
            w, h = img.size
            new_w = 320
            new_h = int(h * (new_w / w))
            img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=60)
            return buf.getvalue()
    except Exception:
        pass
    return None


async def frame_capture_worker():
    """모든 연결된 기기의 화면을 지속적으로 캡처하여 캐시에 저장"""
    while True:
        devices = get_connected_devices()
        for s in devices:
            frame = capture_phone_frame(s)
            if frame:
                frame_cache[s] = frame
        await asyncio.sleep(0.3)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(frame_capture_worker())


@app.get("/api/devices")
async def api_devices():
    devices = get_connected_devices()
    return {"devices": devices, "count": len(devices)}


@app.get("/stream/{serial}")
async def stream_device(serial: str):
    """MJPEG 실시간 스트리밍 엔드포인트"""
    async def frame_generator():
        while True:
            frame = frame_cache.get(serial)
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            await asyncio.sleep(0.25)

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


@app.post("/api/touch")
async def api_touch(req: Request):
    """웹 화면 클릭 좌표를 안드로이드 실제 터치 좌표로 변환하여 주입"""
    data = await req.json()
    serial = data.get("serial")
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    view_w = float(data.get("width", 320))
    view_h = float(data.get("height", 640))
    
    # 기본 해상도 1080 x 2400 (S21/S23)
    target_x = int((x / view_w) * 1080)
    target_y = int((y / view_h) * 2400)
    
    subprocess.run(["adb", "-s", serial, "shell", "input", "tap", str(target_x), str(target_y)], timeout=1)
    # 터치 후 빠른 반응을 위해 즉시 프레임 갱신
    frame = capture_phone_frame(serial)
    if frame:
        frame_cache[serial] = frame
    return {"success": True, "target": [target_x, target_y]}


@app.post("/api/key")
async def api_key(req: Request):
    """키 이벤트 (홈=3, 뒤로=4, 화면켜기=224 등)"""
    data = await req.json()
    serial = data.get("serial")
    key = data.get("key", "home")
    keymap = {"home": "3", "back": "4", "wake": "224", "recents": "187"}
    code = keymap.get(key, "3")
    subprocess.run(["adb", "-s", serial, "shell", "input", "keyevent", code], timeout=1)
    return {"success": True}


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>📱 Phone Farm Live Web Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        body { background: #0f172a; color: #f8fafc; padding: 20px; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 15px; border-bottom: 1px solid #334155; }
        h1 { font-size: 22px; font-weight: 700; color: #38bdf8; display: flex; align-items: center; gap: 10px; }
        .badge { background: #0284c7; color: white; padding: 4px 12px; border-radius: 9999px; font-size: 13px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
        .card { background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; display: flex; flex-direction: column; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); transition: transform 0.15s ease; }
        .card:hover { transform: translateY(-2px); border-color: #38bdf8; }
        .card-header { padding: 10px 14px; background: #0f172a; display: flex; justify-content: space-between; align-items: center; font-size: 13px; font-weight: 600; }
        .card-header .tag { color: #10b981; }
        .screen-container { position: relative; width: 100%; background: #000; cursor: pointer; display: flex; justify-content: center; align-items: center; }
        .screen-container img { width: 100%; height: auto; max-height: 520px; object-fit: contain; display: block; }
        .card-footer { padding: 10px 14px; background: #0f172a; display: flex; justify-content: space-around; gap: 6px; border-top: 1px solid #334155; }
        .btn { background: #334155; border: none; color: #f8fafc; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; transition: background 0.15s; }
        .btn:hover { background: #0284c7; }
        .empty { text-align: center; grid-column: 1 / -1; padding: 60px 20px; color: #94a3b8; font-size: 16px; }
    </style>
</head>
<body>
    <header>
        <h1>📱 폰팜 실시간 웹 대시보드 (Web Live Viewer)</h1>
        <div>
            <span class="badge" id="devCount">감지된 기기: 0대</span>
        </div>
    </header>

    <div class="grid" id="deviceGrid">
        <div class="empty">기기 목록을 불러오는 중입니다...</div>
    </div>

    <script>
        let currentDevices = [];

        async function fetchDevices() {
            try {
                const res = await fetch('/api/devices');
                const data = await res.json();
                document.getElementById('devCount').innerText = `감지된 기기: ${data.count}대`;

                const sorted = data.devices.sort();
                if (JSON.stringify(sorted) !== JSON.stringify(currentDevices)) {
                    currentDevices = sorted;
                    renderGrid();
                }
            } catch (e) {
                console.error(e);
            }
        }

        function renderGrid() {
            const grid = document.getElementById('deviceGrid');
            if (currentDevices.length === 0) {
                grid.innerHTML = '<div class="empty">연결된 폰팜 기기가 없습니다.<br><br>💡 폰팜 박스의 스위치를 <b>USB</b> 모드로 전환해 주세요.</div>';
                return;
            }

            grid.innerHTML = currentDevices.map((s, idx) => `
                <div class="card">
                    <div class="card-header">
                        <span>#${String(idx + 1).padStart(2, '0')} [${s}]</span>
                        <span class="tag">🟢 LIVE</span>
                    </div>
                    <div class="screen-container" onclick="handleTouch(event, '${s}')">
                        <img src="/stream/${s}" alt="${s}">
                    </div>
                    <div class="card-footer">
                        <button class="btn" onclick="sendKey('${s}', 'wake')">💡 켜기</button>
                        <button class="btn" onclick="sendKey('${s}', 'home')">🏠 홈</button>
                        <button class="btn" onclick="sendKey('${s}', 'back')">◀ 뒤로</button>
                        <button class="btn" onclick="sendKey('${s}', 'recents')">📑 앱</button>
                    </div>
                </div>
            `).join('');
        }

        async function handleTouch(evt, serial) {
            const rect = evt.target.getBoundingClientRect();
            const x = evt.clientX - rect.left;
            const y = evt.clientY - rect.top;
            const width = rect.width;
            const height = rect.height;

            await fetch('/api/touch', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ serial, x, y, width, height })
            });
        }

        async function sendKey(serial, key) {
            await fetch('/api/key', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ serial, key })
            });
        }

        setInterval(fetchDevices, 2000);
        fetchDevices();
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, reload=False)
