import asyncio
import os
import edge_tts

VOICE = "zh-CN-YunjianNeural"
OUTPUT_DIR = "public/sounds/sfx"

# I cannot generate music/sfx. I will use speech to simulate them or provide placeholders.
# However, the user asked for specific voice announcements associated with these events.
#
# 1. Player Win Celebration: "*** 玩家胜出！"
# 2. Game Over Summary: "本局头游玩家...获胜分..." (This is dynamic, handled by App.jsx + Web Speech API)
#
# But for the "Sound Effects" (Cheer, Powerful Chord), I will try to find a way.
# Since I can't generate music, I will generate "Speech" that acts as the "Voice" part of the request.
# The user asked: "庆况音效...之后再进行下面的牌局...语音播报: ***玩家胜出"
# So I need the voice file for the static part? 
# "User玩家胜出", "LeftBot玩家胜出"...

async def gen_static_voice():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # 1. Individual Win Announcements
    players = ["User", "LeftBot", "RightBot", "PartnerBot"]
    for p in players:
        text = f"{p}玩家胜出！"
        fname = f"win_{p}.mp3"
        print(f"Generating {fname}...")
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(os.path.join(OUTPUT_DIR, fname))

    # 2. SFX Placeholders (Speech simulation because I can't make real SFX)
    # The user should replace these with real SFX.
    # Cheer
    print("Generating sfx_cheer.mp3 (Speech Placeholder)...")
    await edge_tts.Communicate("哇！太棒了！", VOICE, rate="+20%", pitch="+10Hz").save(os.path.join(OUTPUT_DIR, "sfx_cheer.mp3"))

    # Game Over Chord equivalent (Speech)
    print("Generating sfx_gameover.mp3 (Speech Placeholder)...")
    await edge_tts.Communicate("牌局结束！", VOICE, rate="-10%", pitch="-5Hz").save(os.path.join(OUTPUT_DIR, "sfx_gameover.mp3"))
    
    # Pre-generate some static parts for Game Over if needed?
    # "本局头游玩家"
    # "二游玩家"
    # "三游玩家"
    # "末游玩家"
    # "获胜分"
    parts = {
        "touyou": "本局头游玩家",
        "eryou": "二游玩家",
        "sanyou": "三游玩家",
        "moyou": "末游玩家",
        "score": "获胜分"
    }
    for key, text in parts.items():
        fname = f"announce_{key}.mp3"
        print(f"Generating {fname}...")
        await edge_tts.Communicate(text, VOICE).save(os.path.join(OUTPUT_DIR, fname))

if __name__ == "__main__":
    asyncio.run(gen_static_voice())
