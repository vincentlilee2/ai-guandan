#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量生成掼蛋游戏语音素材
使用 edge-tts 生成高质量中文语音
"""

import asyncio
import edge_tts
import os
from pathlib import Path

# 语音配置
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

# 生成语音文件清单
def generate_sound_list():
    """生成所有需要的语音文件清单"""
    sounds = []
    
    # 1. 过牌
    sounds.append(('pass', '过牌'))
    
    # 2. 单张
    for rank in RANKS:
        text = f"单{RANK_TEXT[rank]}"
        sounds.append((f'single_{rank}', text))
    
    # 3. 对子
    for rank in RANKS:
        text = f"对{RANK_TEXT[rank]}"
        sounds.append((f'pair_{rank}', text))
    
    # 4. 三张
    for rank in RANKS:
        text = f"三张{RANK_TEXT[rank]}"
        sounds.append((f'triple_{rank}', text))
    
    # 5. 组合牌型
    sounds.extend([
        ('triple_pair', '三带二'),
        ('straight', '顺子'),
        ('pairs_straight', '连对'),
        ('plate', '钢板'),
        ('bomb', '炸弹'),
        ('flush', '同花顺'),
        ('rocket', '天王炸'),
        ('play', '出牌')
    ])
    
    return sounds

async def generate_voice(player, filename, text, output_dir):
    """生成单个语音文件"""
    voice = VOICES[player]
    identity = IDENTITIES[player]
    
    # 组合完整播报文本
    if filename == 'pass':
        full_text = f"{identity}过牌"
    else:
        full_text = f"{identity}出牌，{text}"
    
    output_path = output_dir / f"{filename}.mp3"
    
    try:
        communicate = edge_tts.Communicate(full_text, voice)
        await communicate.save(str(output_path))
        print(f"✓ {player}/{filename}.mp3 - {full_text}")
        return True
    except Exception as e:
        print(f"✗ {player}/{filename}.mp3 - Error: {e}")
        return False

async def main():
    """主函数：批量生成所有语音"""
    base_dir = Path(__file__).parent / 'public' / 'sounds'
    
    # 获取语音清单
    sounds = generate_sound_list()
    print(f"共需生成 {len(sounds)} 个语音文件 × 3个玩家 = {len(sounds) * 3} 个文件\n")
    
    # 为每个玩家生成语音
    for player in VOICES.keys():
        player_dir = base_dir / player
        player_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"\n=== 生成 {player} ({IDENTITIES[player]}) 语音 ===")
        
        tasks = []
        for filename, text in sounds:
            tasks.append(generate_voice(player, filename, text, player_dir))
        
        results = await asyncio.gather(*tasks)
        success_count = sum(results)
        print(f"\n{player} 完成: {success_count}/{len(sounds)} 个文件")
    
    print("\n✅ 所有语音生成完毕！")
    print(f"输出目录: {base_dir.absolute()}")

if __name__ == '__main__':
    print("=" * 60)
    print("掼蛋游戏语音素材生成工具")
    print("=" * 60)
    print("\n请确保已安装依赖: pip install edge-tts\n")
    
    asyncio.run(main())
