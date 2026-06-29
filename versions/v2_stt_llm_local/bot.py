# bot.py
import sounddevice as sd
import numpy as np
from faster_whisper import WhisperModel
import ollama
import asyncio
import edge_tts
import pygame
import os
import time

print("Initializing Neural Audio Player...")
pygame.mixer.init()

# Initialize the Transcription Model
print("Loading Whisper model...")
whisper_model = WhisperModel("small",device="cuda", compute_type="int8")
print("Whisper model loaded!")

# 1. Define the System Prompt
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
[ORDER: item_name, quantity]

Example interaction:
Customer: "I want Paneer Handi."
Your response: "Our Paneer Handi is excellent! Just to confirm, would you like me to place an order for one full plate?"
Customer: "Yes, please."
Your response: "Excellent, I've sent one plate of Paneer Handi to the kitchen for you! [ORDER: Paneer Handi, 1]"
"""

# This list is the bot's entire memory. 
chat_history = [
    {"role": "system", "content": SYSTEM_PROMPT}
]

print("System Prompt loaded. Menu updated with 8 items. Memory initialized.")

# 3. Define the Audio Recording Function
def record_audio(duration=5, sample_rate=16000):
    print(f"\n🎤 Listening for {duration} seconds... (Speak now!)")
    
    recording = sd.rec(
        int(duration * sample_rate), 
        samplerate=sample_rate, 
        channels=1, 
        dtype='float32'
    )
    sd.wait() 
    
    audio_data = np.squeeze(recording) 
    
    # Check volume to debug microphone issues
    volume = np.abs(audio_data).mean()
    print(f"📊 Mic Volume Level: {volume:.4f}")
    if volume < 0.001:
        print("⚠️ WARNING: Your microphone is capturing absolute silence. Please check Ubuntu sound settings!")
    
    return audio_data

# 4. Define the Transcription Function
def transcribe_audio(audio_data):
    print("⏳ Transcribing...")
    
    segments, info = whisper_model.transcribe(audio_data, beam_size=10, best_of=5, initial_prompt="""
        Restaurant menu includes:
        Paneer Handi,
        Dal Makhani,
        Veg Dum Biryani,
        Gobi Manchurian,
        Garlic Naan,
        Quinoa Protein Bowl,
        Mini Dosas,
        Mango Lassi
        """)
    
    full_text = ""
    for segment in segments:
        full_text += segment.text
        
    full_text = full_text.strip()
    
    # Filter out Whisper's known "silence hallucinations"
    lower_text = full_text.lower().replace(".", "").replace(",", "").strip()
    if lower_text in ["you", "you you", "thank you", "thanks", ""]:
        return "" 
        
    print(f"👤 Customer: {full_text}")
    return full_text

# --- STREAMING AUDIO ARCHITECTURE ---

# Queue to hold audio files ready to be played
audio_queue = asyncio.Queue()

# Background task to play audio sequentially
async def audio_player_task():
    while True:
        task = await audio_queue.get()
        if task is None:
            break
            
        # Await the generation task to finish downloading the MP3
        audio_file, text = await task
            
        try:
            sound = pygame.mixer.Sound(audio_file)
            duration = sound.get_length()
            delay = (duration * 0.92) / max(len(text), 1)
        except:
            delay = 0.05

        pygame.mixer.music.load(audio_file)
        pygame.mixer.music.play()
        
        # Typewriter effect
        for char in text:
            print(char, end="", flush=True)
            time.sleep(delay)
            
        # Ensure it finishes gracefully
        while pygame.mixer.music.get_busy():
            await asyncio.sleep(0.05)
            
        # Cleanup
        pygame.mixer.music.unload()
        try:
            os.remove(audio_file)
        except:
            pass
        audio_queue.task_done()

# Generator task that creates MP3s and returns the filename
async def generate_audio_task(text, chunk_index):
    voice = "en-IN-PrabhatNeural"
    audio_file = f"temp_response_{chunk_index}.mp3"
    
    communicate = edge_tts.Communicate(text, voice, rate='+42%')
    await communicate.save(audio_file)
    
    # Return the file and text so the player can play it
    return audio_file, text

# Main Async Loop
async def main_loop():
    print("\n--- Voice Bot Started (Press Ctrl+C to stop) ---")
    
    # Start the background audio player
    player_task = asyncio.create_task(audio_player_task())
    
    # Setup Ollama Async Client
    client = ollama.AsyncClient()
    
    while True:
        try:
            audio = record_audio(duration=5)
            user_text = transcribe_audio(audio)
            
            if not user_text:
                continue
                
            chat_history.append({"role": "user", "content": user_text})
            
            print("⏳ AI is thinking...")
            print("🤖 Bot: ", end="", flush=True)
            
            # Start streaming from Ollama
            text_buffer = ""
            full_ai_response = ""
            chunk_index = 0
            generation_tasks = set()
            
            pause_chars = {'.', ',', '?', '!', '\n'}
            
            async for chunk in await client.chat(model="llama3", messages=chat_history, stream=True):
                token = chunk['message']['content']
                full_ai_response += token
                text_buffer += token
                
                # Check for Kitchen Tag Interceptor
                if "[" in text_buffer:
                    continue # Stop processing audio, let it finish streaming
                    
                # If we hit a natural pause, slice the phrase and send to TTS
                if any(p in token for p in pause_chars):
                    phrase = text_buffer.strip()
                    if len(phrase) > 2:
                        task = asyncio.create_task(generate_audio_task(phrase + " ", chunk_index))
                        generation_tasks.add(task)
                        task.add_done_callback(generation_tasks.discard)
                        
                        # Push the task itself into the queue to preserve strict chronological order!
                        await audio_queue.put(task)
                        chunk_index += 1
                    
                    # Clear buffer for the next phrase
                    text_buffer = ""
            
            # Handle final leftovers
            if text_buffer.strip() and "[" not in text_buffer:
                task = asyncio.create_task(generate_audio_task(text_buffer.strip() + " ", chunk_index))
                generation_tasks.add(task)
                task.add_done_callback(generation_tasks.discard)
                await audio_queue.put(task)
                
            # Wait for all MP3s to finish downloading from Microsoft
            if generation_tasks:
                await asyncio.wait(generation_tasks)
                
            # Wait for the Pygame audio queue to be completely empty and finished playing
            await audio_queue.join()
            
            # Print a newline after the typewriter effect finishes
            print() 
            
            # Debug: Print the raw, unsanitized response directly from the LLM
            print(f"\n[DEBUG RAW LLM RESPONSE]: {full_ai_response}")
                
            # Intercept and process any ORDER tags AFTER the audio finishes typing to prevent visual interruption
            if "[ORDER:" in full_ai_response:
                parts = full_ai_response.split("[ORDER:")
                order_data = parts[1].replace("]", "").strip()
                
                print("\n" + "="*50)
                print("🚨 KITCHEN ALERT: NEW ORDER RECEIVED 🚨")
                print(f"📦 ITEM & QTY: {order_data}")
                print("="*50 + "\n")
                
            # Save the AI response to memory, BUT strip out the ORDER tag so it doesn't corrupt the bot's memory!
            if "[ORDER:" in full_ai_response:
                clean_memory = full_ai_response.split("[ORDER:")[0].strip()
                chat_history.append({"role": "assistant", "content": clean_memory})
                
                # INJECT A SYSTEM NOTE so the LLM knows the order was successfully completed
                chat_history.append({
                    "role": "system", 
                    "content": f"SYSTEM NOTE: You have successfully placed the order for {order_data}. DO NOT ask the customer to confirm this order again. Ask if they need anything else."
                })
            else:
                chat_history.append({"role": "assistant", "content": full_ai_response})
            
        except KeyboardInterrupt:
            print("\nShutting down Voice Bot...")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            break
            
    # Cleanup background task
    await audio_queue.put(None)

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
