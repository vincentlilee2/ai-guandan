#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重新生成 User 结算语音（sounds/User/）：
- 音色：zh-CN-XiaoyiNeural（年轻女声，与对家 Xiaoxiao 区分，形成 2 男 2 女）
- 文案：名次版（第一名/第X名），消除「一三游/三游」连读模糊
- 生成前备份原文件为 *.mp3.bak
"""
import asyncio, edge_tts, shutil, os
from pathlib import Path

VOICE = "zh-CN-XiaoyiNeural"
DIR = Path(__file__).parent / "public" / "sounds" / "User"

TEXTS = {
    "end_team_300": "本局结束。你和队友获得第一名和第二名。你们各得三百分。",
    "end_team_200": "本局结束。你和队友获得第一名和第三名。你们各得两百分。",
    "end_team_100": "本局结束。你和队友获得第一名和第四名。你们各得一百分。",
    "end_lose_300": "本局结束。对手获得第一名和第二名。你们本局失败，扣三百分。",
    "end_lose_200": "本局结束。对手获得第一名和第三名。你们本局失败，扣两百分。",
    "end_lose_100": "本局结束。对手获得第一名和第四名。你们本局失败，扣一百分。",
    "congrat_rank1": "恭喜你获得第一名！",
    "congrat_rank2": "恭喜你获得第二名！",
    "congrat_rank3": "恭喜你获得第三名！",
}

async def gen_one(name, text):
    out = DIR / f"{name}.mp3"
    bak = DIR / f"{name}.mp3.bak"
    if out.exists() and not bak.exists():
        shutil.copy2(out, bak)
        print(f"备份: {name}.mp3 -> .bak")
    await edge_tts.Communicate(text, VOICE).save(str(out))
    print(f"✓ {name}.mp3  <- {text}  (voice={VOICE})")

async def main():
    DIR.mkdir(parents=True, exist_ok=True)
    for name, text in TEXTS.items():
        try:
            await gen_one(name, text)
        except Exception as e:
            print(f"✗ {name} 失败: {e}")

if __name__ == "__main__":
    asyncio.run(main())
