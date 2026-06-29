import json
import asyncio
import io
import wave
import numpy as np
from pydub import AudioSegment
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from faster_whisper import WhisperModel
import ollama
import edge_tts

app = FastAPI()

# ---------------------------------------------------------
# GLOBAL MODEL INITIALIZATION
# ---------------------------------------------------------
print("Loading Whisper model into GPU memory...")
try:
    whisper_model = WhisperModel("small", device="cuda", compute_type="int8")
    print("Whisper model loaded successfully!")
except Exception as e:
    print(f"Failed to load Whisper: {e}")
    whisper_model = None

# ---------------------------------------------------------
# KITCHEN DASHBOARD INITIALIZATION
# ---------------------------------------------------------
DASHBOARD_FILE = "manager_dashboard.html"

def init_dashboard():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>PIDPEN Kitchen Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600;800&display=swap" rel="stylesheet">
    <style>
        body {
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
            color: #fff;
            font-family: 'Outfit', sans-serif;
            min-height: 100vh;
        }
        h1 {
            text-align: center;
            font-size: 3em;
            text-shadow: 0 4px 10px rgba(0,0,0,0.5);
            margin-bottom: 40px;
        }
        #orders-container {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            max-width: 1200px;
            margin: 0 auto;
        }
        .order-card {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(15px);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 16px;
            padding: 25px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        .order-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 5px;
            height: 100%;
            background: #ff4757;
        }
        .order-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
        }
        .table-badge {
            background: #ff4757;
            color: white;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.9em;
            font-weight: 800;
            display: inline-block;
            margin-bottom: 15px;
        }
        .item-name {
            font-size: 1.5em;
            font-weight: 800;
            color: #ffa502;
            margin: 0 0 10px 0;
        }
        .qty {
            font-size: 1.1em;
            font-weight: 600;
            color: #dfe4ea;
        }
        .extra-info {
            margin-top: 15px;
            padding-top: 15px;
            border-top: 1px solid rgba(255,255,255,0.1);
            font-style: italic;
            color: #7bed9f;
        }
    </style>
</head>
<body>
    <h1>👨‍🍳 Active Kitchen Orders</h1>
    <div id="orders-container">
"""
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(html_content)
        
# Erase and create a fresh dashboard every time the server boots up
init_dashboard()

# ---------------------------------------------------------
# TRANSCODING UTILITIES
# ---------------------------------------------------------
def convert_pcm_to_float32(raw_pcm_bytes: bytes) -> np.ndarray:
    """Upstream: Converts raw 16-bit PCM from ESP32 into a float32 NumPy array for Whisper."""
    audio_data = np.frombuffer(raw_pcm_bytes, dtype=np.int16)
    return audio_data.astype(np.float32) / 32768.0

def convert_mp3_to_pcm(mp3_bytes: bytes) -> bytes:
    """Downstream: Converts Edge-TTS MP3 into raw 16kHz, 16-bit, Mono PCM for the ESP32 speaker."""
    audio = AudioSegment.from_file(io.BytesIO(mp3_bytes), format="mp3")
    audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    return audio.raw_data

# ---------------------------------------------------------
# SYSTEM KNOWLEDGE
# ---------------------------------------------------------
SYSTEM_PROMPT = """
You are a polite, natural-sounding waiter at a modern restaurant. Your only job is to assist customers with the menu, answer food questions, and take orders. Your name is VoiceBot. And you work at a restaurant called "PIDPEN Kitchen". 
Do not use lists or markdown. Speak in short only one to one and half, conversational sentences as if you are talking out loud. If a customer asks about topics outside the restaurant, politely redirect them to the menu. Don't need to overexplain thing just give them the information they need to make a decision. Don't tell him whole menu or number of items in the menu unless he asks for it

keep conversations very natural and human-like, also you can add humor. Make shorter when no need to be long.
RESTAURANT KNOWLEDGE (OUR MENU):

1. Paneer Handi (Signature Dish)
- Details: A rich curry made with paneer cubes.
- Important Note: We do NOT use any onions. It is made with a special tomato and cashew gravy.

2. Dal Makhani
- Details: Black lentils slow-cooked for 12 hours. 
- Important Note: Contains dairy (heavy cream and butter). It is very rich and mildly spiced.

3. Veg Dum Biryani
- Details: Fragrant basmati rice cooked with mixed vegetables and aromatic spices. 
- Important Note: Served with a side of cool cucumber raita. It is medium spicy.

4. Gobi Manchurian (Vegan Option)
- Details: Crispy cauliflower florets tossed in a spicy, sweet, and tangy Indo-Chinese sauce.
- Important Note: This is 100% vegan and dairy-free.

5. Garlic Naan
- Details: Flatbread baked in a tandoor oven, topped with minced garlic and butter.
- Important Note: Contains gluten and dairy. 

6. Chana Masala (Healthy Option)
- Details: A hearty chickpea curry cooked with tomatoes, onions, and a blend of spices. 
- Important Note: This dish is vegan and packed with protein. It is moderately spicy, but we can adjust the heat level upon request.

7. Mini Dosas (Kids Menu)
- Details: Small, crispy savory crepes made from rice and lentil batter.
- Important Note: Very mild, not spicy at all. Perfect for children.

8. Mango Lassi (Beverage)
- Details: A sweet, thick yogurt-based drink blended with fresh mango pulp.
- Important Note: Contains dairy. Very refreshing and helps cool down spicy food.

ORDERING INSTRUCTIONS:
You must strictly follow a 2-step confirmation process for all orders to prevent mistakes.
STEP 1: If the customer asks for a dish, you MUST NOT generate the [ORDER:] tag. You are STRICTLY FORBIDDEN from generating the [ORDER:] tag in this step. You must only ask them to explicitly confirm (e.g., "Just to confirm, would you like to place an order for one Dal Makhani?").
STEP 2: ONLY after the customer explicitly answers "Yes" or "Confirm" to your question in the NEXT turn, you will acknowledge the order naturally, and THEN you may append the strict data tag at the very end of your response.

The data tag must be formatted exactly like this:
[ORDER: item_name, quantity, extra_info]
(For extra_info, provide 5-8 words of customer preferences like "make it very spicy" or "no onions". If none, write "No special requests")

Example interaction:
Customer: "I want Paneer Handi and make it very spicy."
Your response: "Our Paneer Handi is excellent! Just to confirm, would you like me to place an order for one full plate?"
Customer: "Yes, please."
Your response: "Excellent, I've sent one plate of Paneer Handi to the kitchen for you! [ORDER: Paneer Handi, 1, very spicy as requested]"
"""

# ---------------------------------------------------------
# CONNECTION MANAGER
# ---------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_tables: dict[str, WebSocket] = {}
        self.sessions: dict[str, list] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        # Finished WAV files ready for HTTP serving
        self.audio_ready: dict[str, bytes] = {}

    def disconnect(self, table_id: str):
        if table_id in self.active_tables:
            del self.active_tables[table_id]
            print(f"[-] SERVER: {table_id} disconnected and removed from active pool.")
        if table_id in self.locks:
            del self.locks[table_id]

    async def send_signal(self, table_id: str, event: str):
        """Sends a tiny lightweight JSON control message over WebSocket."""
        if table_id in self.active_tables:
            try:
                websocket = self.active_tables[table_id]
                await websocket.send_text(json.dumps({"event": event}))
            except Exception as e:
                print(f"[!] SERVER: Failed to signal {table_id}: {e}")

    # Keep the old send_binary_audio method commented out for reference
    # async def send_binary_audio(self, table_id: str, audio_bytes: bytes): ...

manager = ConnectionManager()

# ---------------------------------------------------------
# QUEUE 3: THE SPEAKER (Edge-TTS)
# ---------------------------------------------------------

def splice_pcm_chunks(chunks: list[bytes], crossfade_ms: int = 8, sample_rate: int = 16000) -> bytes:
    """Concatenate 16-bit mono PCM chunks with a short crossfade at each boundary
    to eliminate clicks from discontinuous waveforms."""
    chunks = [c for c in chunks if c]
    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]

    fade_samples = int(sample_rate * crossfade_ms / 1000)
    result = np.frombuffer(chunks[0], dtype=np.int16).astype(np.float32)

    for next_chunk in chunks[1:]:
        next_arr = np.frombuffer(next_chunk, dtype=np.int16).astype(np.float32)
        n = min(fade_samples, len(result), len(next_arr))

        if n > 0:
            fade_out = np.linspace(1.0, 0.0, n)
            fade_in = np.linspace(0.0, 1.0, n)
            blended = result[-n:] * fade_out + next_arr[:n] * fade_in
            result = np.concatenate([result[:-n], blended, next_arr[n:]])
        else:
            result = np.concatenate([result, next_arr])

    return result.astype(np.int16).tobytes()

async def process_tts_worker(table_id: str, text: str) -> bytes:
    """Generates MP3 audio and returns PCM bytes (no shared-state mutation here)."""
    try:
        voice = "en-IN-PrabhatNeural"
        communicate = edge_tts.Communicate(text, voice, rate='+42%')
        
        mp3_buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_buffer.extend(chunk["data"])
                
        if mp3_buffer:
            raw_pcm = convert_mp3_to_pcm(bytes(mp3_buffer))
            print(f"[{table_id}] 🎵 TTS generated {len(raw_pcm)} bytes for: '{text.strip()[:40]}...'")
            return raw_pcm
        return b""
    except Exception as e:
        print(f"[!] [{table_id}] TTS Error: {e}")
        return b""

# ---------------------------------------------------------
# QUEUE 2: THE THINKER (Ollama)
# ---------------------------------------------------------
async def process_llm_worker(table_id: str):
    """Streams LLM tokens, slices sentences, and triggers TTS workers."""
    chat_history = manager.sessions.get(table_id, [])
    
    print(f"[{table_id}] ⏳ AI is thinking...")
    client = ollama.AsyncClient()
    
    text_buffer = ""
    full_ai_response = ""
    pause_chars = {'.', ',', '?', '!', '\n'}
    tts_tasks = []
    
    try:
        async for chunk in await client.chat(model="llama3", messages=chat_history, stream=True):
            token = chunk['message']['content']
            full_ai_response += token
            text_buffer += token
            
            # Check for Kitchen Tag Interceptor
            if "[" in text_buffer:
                continue
                
            # Slice upon punctuation and send to Queue 3
            if any(p in token for p in pause_chars):
                phrase = text_buffer.strip()
                if len(phrase) > 2:
                    print(f"[{table_id}] ✂️ Sentence sliced: '{phrase}' -> Passing to TTS Generator!")
                    tts_tasks.append(asyncio.create_task(process_tts_worker(table_id, phrase + " ")))
                text_buffer = ""
                
        # Flush leftovers
        if text_buffer.strip() and "[" not in text_buffer:
            print(f"[{table_id}] ✂️ Final leftover sliced: '{text_buffer.strip()}' -> Passing to TTS Generator!")
            tts_tasks.append(asyncio.create_task(process_tts_worker(table_id, text_buffer.strip() + " ")))
            
        print(f"[{table_id}] [DEBUG RAW]: {full_ai_response}")
            
        # Parse Orders
        if "[ORDER:" in full_ai_response:
            parts = full_ai_response.split("[ORDER:")
            order_data = parts[1].replace("]", "").strip()
            
            # Safely split by comma and extract details
            order_parts = [p.strip() for p in order_data.split(",")]
            item_name = order_parts[0] if len(order_parts) > 0 else "Unknown Item"
            qty = order_parts[1] if len(order_parts) > 1 else "1"
            extra_info = order_parts[2] if len(order_parts) > 2 else "No special requests"
            
            print("\n" + "="*50)
            print("🚨 KITCHEN ALERT: NEW ORDER RECEIVED 🚨")
            print(f"📦 TABLE: {table_id} | ITEM: {item_name} | QTY: {qty} | INFO: {extra_info}")
            print("="*50 + "\n")
            
            # Dynamically inject a beautiful HTML card into the Manager Dashboard
            card_html = f"""
        <div class="order-card">
            <div class="table-badge">{table_id.upper()}</div>
            <div class="time">Just now</div>
            <div class="item-name">{item_name}</div>
            <div class="qty">Quantity: {qty}</div>
            <div class="extra-info">"{extra_info}"</div>
        </div>
"""
            try:
                with open(DASHBOARD_FILE, "a", encoding="utf-8") as f:
                    f.write(card_html)
            except Exception as e:
                print(f"[!] Failed to write to dashboard: {e}")
            
            clean_memory = full_ai_response.split("[ORDER:")[0].strip()
            chat_history.append({"role": "assistant", "content": clean_memory})
            
            chat_history.append({
                "role": "system", 
                "content": f"SYSTEM NOTE: You have successfully placed the order for {order_data}. DO NOT ask the customer to confirm this order again. Ask if they need anything else."
            })
        else:
            chat_history.append({"role": "assistant", "content": full_ai_response})
            
        # Wait for all TTS workers — gather preserves order of the input list
        pcm_chunks = []
        if tts_tasks:
            pcm_chunks = await asyncio.gather(*tts_tasks)
        
        # Concatenate in original sentence order, crossfade-splice to avoid clicks
        pcm_bytes = splice_pcm_chunks(list(pcm_chunks))
        
        if pcm_bytes:
            wav_io = io.BytesIO()
            with wave.open(wav_io, 'wb') as wav_file:
                wav_file.setnchannels(1)      # Mono
                wav_file.setsampwidth(2)      # 16-bit
                wav_file.setframerate(16000)  # 16kHz
                wav_file.writeframes(pcm_bytes)
            manager.audio_ready[table_id] = wav_io.getvalue()
            
            # --- DEBUG EXPORT ---
            debug_filename = f"debug_server_audio_{table_id}.wav"
            with open(debug_filename, "wb") as f:
                f.write(manager.audio_ready[table_id])
            print(f"[{table_id}] 💾 Saved assembled server audio to '{debug_filename}' for playback debugging!")
            # --------------------
            
            print(f"[{table_id}] ✅ Audio file ready ({len(manager.audio_ready[table_id])} bytes WAV). Signaling ESP32...")
            
            # Send a tiny, lightweight signal over WebSocket — no binary data, no crashes!
            await manager.send_signal(table_id, "audio_ready")
        
        print("\n" + "="*60)
        print(f"✅ [{table_id}] AUDIO PIPELINE COMPLETE!")
        print("🎤 The AI is quiet. You may now press the button to speak.")
        print("="*60 + "\n")
            
    except Exception as e:
        print(f"[!] [{table_id}] LLM Error: {e}")

# ---------------------------------------------------------
# QUEUE 1: THE TRANSCRIBER (Whisper)
# ---------------------------------------------------------
async def process_audio_worker(table_id: str, raw_pcm_bytes: bytes):
    """Converts PCM, transcodes to float32, transcribes, and triggers LLM worker."""
    if whisper_model is None:
        print(f"[!] [{table_id}] Whisper model not loaded!")
        return

    try:
        print(f"[{table_id}] 🎙️ Transcribing audio...")
        
        # --- DEBUG EXPORT ---
        debug_filename = f"debug_mic_{table_id}.wav"
        with wave.open(debug_filename, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(raw_pcm_bytes)
        print(f"[{table_id}] 💾 Saved raw ESP32 audio to '{debug_filename}' for playback!")
        # --------------------
        
        float_audio = convert_pcm_to_float32(raw_pcm_bytes)
        
        # Whisper blocks the CPU/GPU, so we must offload it to a separate thread
        loop = asyncio.get_running_loop()
        def run_whisper():
            segments, _ = whisper_model.transcribe(float_audio, beam_size=5)
            return " ".join([segment.text for segment in segments])
            
        user_text = await loop.run_in_executor(None, run_whisper)
        user_text = user_text.strip()
        
        if user_text:
            print(f"[{table_id}] 🗣️ Customer: {user_text}")
            if table_id in manager.sessions:
                manager.sessions[table_id].append({"role": "user", "content": user_text})
                # Trigger Thinker
                #asyncio.create_task(process_llm_worker(table_id))
        else:
            print(f"[{table_id}] 🗣️ Customer: [Inaudible / Empty]")
            
    except Exception as e:
        print(f"[!] [{table_id}] STT Error: {e}")

# ---------------------------------------------------------
# HTTP AUDIO SERVING ENDPOINT
# ---------------------------------------------------------
@app.get("/audio/{table_id}")
async def serve_audio(table_id: str):
    """ESP32 calls this HTTP GET to download the complete AI response as a WAV file."""
    if table_id not in manager.audio_ready or not manager.audio_ready[table_id]:
        return {"error": "No audio ready for this table"}
    
    wav_bytes = manager.audio_ready[table_id]
    print(f"[{table_id}] 📡 ESP32 is downloading audio ({len(wav_bytes)} bytes)...")
    
    # Return standard Response so FastAPI preserves the exact Content-Length
    # and avoids 'Transfer-Encoding: chunked' which breaks ESP32 HTTPClient.getSize()
    return Response(
        content=wav_bytes,
        media_type="audio/wav"
    )

# ---------------------------------------------------------
# WEBSOCKET ENDPOINT
# ---------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    table_id = None
    try:
        first_message = await websocket.receive_text()
        data = json.loads(first_message)
        
        if data.get("event") == "connect":
            table_id = data.get("table")
            if table_id:
                manager.active_tables[table_id] = websocket
                manager.locks[table_id] = asyncio.Lock()
                manager.audio_ready[table_id] = b""
                
                if table_id not in manager.sessions:
                    manager.sessions[table_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
                    print(f"[*] SERVER: Created new memory session for {table_id}.")
                    
                print(f"[+] SERVER: {table_id} registered and actively listening.")
            else:
                await websocket.close(code=1008, reason="Missing table ID in handshake")
                return
        else:
            await websocket.close(code=1008, reason="Invalid handshake event")
            return
            
        audio_buffer = bytearray()
        
        while True:
            message = await websocket.receive()
            
            if "bytes" in message:
                audio_chunk = message["bytes"]
                audio_buffer.extend(audio_chunk)
                
            elif "text" in message:
                text_data = json.loads(message["text"])
                
                if text_data.get("event") == "stop_stream":
                    print(f"[{table_id}] Button Released! Captured {len(audio_buffer)} total bytes of audio.")
                    
                    final_audio_payload = bytes(audio_buffer)
                    audio_buffer.clear()
                    
                    # Reset the audio ready buffer for this new turn
                    manager.audio_ready[table_id] = b""
                    
                    # Fire off the AI Processing Pipeline
                    print(f"[{table_id}] --> Forwarded payload to AI Pipeline Worker.")
                    asyncio.create_task(process_audio_worker(table_id, final_audio_payload))

    except WebSocketDisconnect:
        if table_id:
            manager.disconnect(table_id)
    except Exception as e:
        print(f"[!] SERVER: Error on connection {table_id}: {e}")
        if table_id:
            manager.disconnect(table_id)

# To run this server from the terminal:
# uvicorn server:app --host 0.0.0.0 --port 8000
