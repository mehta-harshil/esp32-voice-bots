import sounddevice as sd
import queue
import sys
import os
import json
from vosk import Model, KaldiRecognizer

# 1. Initialize the Queue (The waiting line for audio chunks)
audio_queue = queue.Queue()

# 2. Define the Callback Function
# This function runs in the background. Every time the microphone captures 
# a tiny slice of audio, it throws it into our queue.
def audio_callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(bytes(indata))

# 3. Load the Vosk Model
print("Loading Real-Time Model...")
# Use os.path to correctly resolve the models/model path from the project root
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(os.path.dirname(BASE_DIR), "models", "model")
model = Model(MODEL_DIR) 
recognizer = KaldiRecognizer(model, 16000)

print("\n🎤 REAL-TIME TRANSCRIPTION ACTIVE (Speak now, press Ctrl+C to stop) 🎤\n")

# 4. Open the Continuous Audio Stream
# We use RawInputStream to stream audio continuously without blocking the code
with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype='int16',
                       channels=1, callback=audio_callback):
    
    # 5. The Real-Time Processing Loop
    while True:
        # Grab the latest tiny chunk of audio from the queue
        data = audio_queue.get()
        
        # Pass the chunk to the recognizer
        if recognizer.AcceptWaveform(data):
            # THE FINAL RESULT (When you take a breath/pause)
            result = json.loads(recognizer.Result())
            if result["text"]:
                # Print the final confirmed sentence on a new line
                print(f"\n[FINAL]: {result['text']}\n")
        else:
            # THE PARTIAL RESULT (As you are actively speaking)
            partial = json.loads(recognizer.PartialResult())
            if partial["partial"]:
                # THE MAGIC TRICK: We use '\r' (Carriage Return). 
                # This forces the terminal to overwrite the current line instead 
                # of printing a new line. This creates the "live typing" effect!
                sys.stdout.write(f"\r[LIVE]: {partial['partial']}")
                sys.stdout.flush() # Force the terminal to update instantly