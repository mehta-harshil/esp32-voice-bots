import json
import asyncio
import numpy as np
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from faster_whisper import WhisperModel

app = FastAPI()

# =========================================================
# 1. HTML DASHBOARD (Embedded)
# =========================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Live STT Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        body {
            margin: 0; padding: 20px;
            font-family: 'Inter', sans-serif;
            background-color: #0f172a; color: #f8fafc;
        }
        h1 { text-align: center; color: #38bdf8; margin-bottom: 30px; }
        .grid {
            display: flex; flex-direction: column; gap: 15px;
            max-width: 900px; margin: 0 auto;
        }
        .card {
            background: #1e293b; border-left: 5px solid #10b981;
            padding: 20px; border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            animation: slideIn 0.3s ease-out;
        }
        .card-header {
            display: flex; justify-content: space-between;
            font-size: 0.9em; color: #94a3b8; margin-bottom: 10px;
            border-bottom: 1px solid #334155; padding-bottom: 9px;
        }
        .table-badge {
            background: #3b82f6; color: white;
            padding: 3px 10px; border-radius: 12px; font-weight: bold;
        }
        .transcript { font-size: 1.2em; font-weight: 600; line-height: 1.5; color: #e2e8f0; }
        .empty-state { text-align: center; color: #64748b; margin-top: 50px; font-style: italic; }
        @keyframes slideIn {
            from { opacity: 0; transform: translateY(-20px); }
            to { opacity: 1; transform: translateY(0); }
        }
    </style>
</head>
<body>
    <h1>🎙️ Live Waiter Transcriptions</h1>
    <div class="grid" id="transcription-container">
        <div class="empty-state" id="empty-state">Waiting for incoming speech...</div>
    </div>

    <script>
        // Connect to the dashboard WebSocket
        const ws = new WebSocket(`ws://${window.location.host}/dashboard_ws`);
        const container = document.getElementById('transcription-container');
        const emptyState = document.getElementById('empty-state');

        ws.onopen = () => console.log("Connected to Real-Time Server");
        
        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            
            // Remove empty state text if it exists
            if (emptyState) emptyState.remove();

            // Create new card HTML
            const card = document.createElement('div');
            card.className = 'card';
            card.innerHTML = `
                <div class="card-header">
                    <span class="table-badge">${data.table_id.toUpperCase()}</span>
                    <span>🕒 ${data.date} | ${data.time}</span>
                </div>
                <div class="transcript">" ${data.text} "</div>
            `;

            // Insert at the top of the list
            container.insertBefore(card, container.firstChild);
        };

        ws.onclose = () => console.log("Connection lost. Refresh the page.");
    </script>
</body>
</html>
"""

# =========================================================
# 2. MODEL & MANAGERS
# =========================================================
print("Loading Whisper model...")
try:
    whisper_model = WhisperModel("small", device="cuda", compute_type="int8")
    print("Whisper loaded!")
except Exception as e:
    print(f"Failed to load Whisper: {e}")
    whisper_model = None

# Manages active website dashboard connections
class DashboardManager:
    def __init__(self):
        self.active_dashboards: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_dashboards.append(websocket)
        print("[+] UI Dashboard Connected")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_dashboards:
            self.active_dashboards.remove(websocket)
            print("[-] UI Dashboard Disconnected")

    async def broadcast(self, payload: dict):
        # Send the data to every open website tab
        for ws in self.active_dashboards:
            try:
                await ws.send_json(payload)
            except:
                pass

# Manages active ESP32 microphone connections
class DeviceManager:
    def __init__(self):
        self.active_tables: dict[str, WebSocket] = {}

    def disconnect(self, table_id: str):
        if table_id in self.active_tables:
            del self.active_tables[table_id]
            print(f"[-] {table_id} disconnected.")

dash_manager = DashboardManager()
dev_manager = DeviceManager()

def convert_pcm_to_float32(raw_pcm_bytes: bytes) -> np.ndarray:
    audio_data = np.frombuffer(raw_pcm_bytes, dtype=np.int16)
    return audio_data.astype(np.float32) / 32768.0

# =========================================================
# 3. TRANSCRIPTION WORKER
# =========================================================
async def process_audio_worker(table_id: str, raw_pcm_bytes: bytes):
    if whisper_model is None: return

    try:
        print(f"[{table_id}] 🎙️ Transcribing...")
        float_audio = convert_pcm_to_float32(raw_pcm_bytes)
        
        loop = asyncio.get_running_loop()
        def run_whisper():
            segments, _ = whisper_model.transcribe(float_audio, beam_size=5)
            return " ".join([segment.text for segment in segments])
            
        user_text = await loop.run_in_executor(None, run_whisper)
        user_text = user_text.strip()
        
        if user_text:
            now = datetime.now()
            
            # Print to terminal
            print(f"\n[{table_id}] -> {user_text}\n")
            
            # BROADCAST TO WEBSITE!
            await dash_manager.broadcast({
                "table_id": table_id,
                "date": now.strftime("%b %d, %Y"), # e.g., Oct 24, 2023
                "time": now.strftime("%I:%M:%S %p"), # e.g., 04:30:15 PM
                "text": user_text
            })
            
    except Exception as e:
        print(f"[!] STT Error: {e}")

# =========================================================
# 4. API ENDPOINTS
# =========================================================

# Endpoint 1: The Website URL
@app.get("/")
async def serve_dashboard():
    """When you open the server IP in a browser, it loads the HTML dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)

# Endpoint 2: The Website WebSocket
@app.websocket("/dashboard_ws")
async def dashboard_websocket(websocket: WebSocket):
    """The website connects here to receive live updates."""
    await dash_manager.connect(websocket)
    try:
        while True:
            # We don't expect messages from the UI, just keeping connection open
            await websocket.receive_text()
    except WebSocketDisconnect:
        dash_manager.disconnect(websocket)

# Endpoint 3: The ESP32 WebSocket
@app.websocket("/ws")
async def esp32_websocket(websocket: WebSocket):
    """The ESP32 connects here to send audio."""
    await websocket.accept()
    table_id = None
    try:
        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        
        if data.get("event") == "connect":
            table_id = data.get("table")
            dev_manager.active_tables[table_id] = websocket
            
        audio_buffer = bytearray()
        
        while True:
            message = await websocket.receive()
            if "bytes" in message:
                audio_buffer.extend(message["bytes"])
            elif "text" in message:
                text_data = json.loads(message["text"])
                if text_data.get("event") == "stop_stream":
                    final_audio = bytes(audio_buffer)
                    audio_buffer.clear()
                    asyncio.create_task(process_audio_worker(table_id, final_audio))

    except WebSocketDisconnect:
        if table_id: dev_manager.disconnect(table_id)
    except Exception as e:
        if table_id: dev_manager.disconnect(table_id)