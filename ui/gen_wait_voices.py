#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 AI 催促（稍等，让我想想）语音
"""

import asyncio
import edge_tts
from pathlib import Path

# 语音配置 (保持与 generate_sounds.py 一致)
VOICES = {
    'RightBot': 'zh-CN-YunxiNeural',      # 下家 - 男声
    'PartnerBot': 'zh-CN-XiaoxiaoNeural',  # 对家 - 女声
    'LeftBot': 'zh-CN-YunyangNeural'       # 上家 - 男声
}

# 身份称呼
IDENTITIES = {
    'RightBot': '下家',
    'PartnerBot': '对家',
    'LeftBot': '上家'
}

TEXT = "稍等，让我想想"
FILENAME = "wait_thinking.mp3"

async def generate_wait_voice(player, output_dir):
    voice = VOICES[player]
    identity = IDENTITIES[player]
    full_text = f"{identity}，{TEXT}"
    
    output_path = output_dir / player / FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        communicate = edge_tts.Communicate(full_text, voice)
        await communicate.save(str(output_path))
        print(f"✓ {player}/{FILENAME} - '{full_text}' (Voice: {voice})")
        return True
    except Exception as e:
        print(f"✗ {player}/{FILENAME} - Error: {e}")
        return False

async def main():
    base_dir = Path(__file__).parent / 'public' / 'sounds'
    
    print(f"正在生成 AI 思考语音...")
    tasks = []
    for player in VOICES.keys():
        tasks.append(generate_wait_voice(player, base_dir))
    
    await asyncio.gather(*tasks)
    print("\n生成完毕！")

if __name__ == "__main__":
    asyncio.run(main())
