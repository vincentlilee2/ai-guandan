#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成缺失的三带二语音文件 (sandaier_{rank}.mp3)
"""

import asyncio
import edge_tts
import os
from pathlib import Path

# 语音配置 (Alignment with generate_sounds.py)
VOICES = {
    'RightBot': 'zh-CN-YunxiNeural',      # 男声-30岁
    'PartnerBot': 'zh-CN-XiaoxiaoNeural',  # 女声-25岁
    'LeftBot': 'zh-CN-YunyangNeural'       # 男声-40岁
}

# 牌面映射
RANKS = ['3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A', '2', 'joker_s', 'joker_b']
RANK_TEXT = {
    '3': '三', '4': '四', '5': '五', '6': '六', '7': '七', '8': '八', 
    '9': '九', '10': '十', 'J': 'J', 'Q': 'Q', 'K': 'K', 'A': 'A', '2': '二',
    'joker_s': '小王', 'joker_b': '大王'
}

# 身份称呼
IDENTITIES = {
    'RightBot': '下家',
    'PartnerBot': '对家',
    'LeftBot': '上家'
}

async def generate_voice(player, filename, text, output_dir):
    voice = VOICES[player]
    identity = IDENTITIES[player]
    
    # 构建完整播报文本
    # 注意：generate_sounds.py 的逻辑是 `f"{identity}出牌，{text}"`
    full_text = f"{identity}出牌，{text}"
    
    output_path = output_dir / f"{filename}.mp3"
    
    # Ensure directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        communicate = edge_tts.Communicate(full_text, voice)
        await communicate.save(str(output_path))
        print(f"✓ {player}/{filename}.mp3 -> {full_text}")
    except Exception as e:
        print(f"✗ {player}/{filename}.mp3 - Error: {e}")

async def main():
    base_dir = Path(__file__).parent / 'public' / 'sounds'
    
    tasks = []
    
    for player in VOICES.keys():
        player_dir = base_dir / player
        
        for rank in RANKS:
            filename = f"sandaier_{rank}"
            # 文本生成的逻辑： 三张+点数+带二
            # 例如： "三张五带二", "三张J带二"
            card_text = RANK_TEXT[rank]
            text = f"三张{card_text}带二"
            
            tasks.append(generate_voice(player, filename, text, player_dir))
            
    print(f"开始生成 {len(tasks)} 个缺失的三带二语音文件...")
    await asyncio.gather(*tasks)
    print("生成完成！")

if __name__ == "__main__":
    loop = asyncio.get_event_loop_policy().get_event_loop()
    loop.run_until_complete(main())
