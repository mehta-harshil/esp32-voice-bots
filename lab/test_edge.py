import asyncio
import edge_tts

async def main():
    communicate = edge_tts.Communicate("Hello", "en-IN-PrabhatNeural")
    formats = await edge_tts.list_voices()
    print("Voices listed")

asyncio.run(main())
