# Rules for the game backend
# backend/rules.py
from enum import IntEnum
from collections import defaultdict
from typing import List, Dict
from .models import Card, Rank, Suit

# 定义牌型层级，值越大越厉害
class CardType(IntEnum):
    INVALID = 0
    SINGLE = 1          # 单张
    PAIR = 2            # 对子
    TRIPLET = 3         # 三张 (通常不单独出，除非最后)
    TRIPLET_PAIR = 4    # 三带二
    STRAIGHT = 5        # 顺子 (严格5张)
    CONSECUTIVE_PAIRS = 6 # 连对 (3连对)
    CONSECUTIVE_TRIPLETS = 7 # 钢板 (2连三张)
    
    # --- 强力牌型分割线 ---
    BOMB_4 = 20         # 4张炸
    BOMB_5 = 21         # 5张炸
    STRAIGHT_FLUSH = 30 # 同花顺 (规则：>5炸, <6炸)
    BOMB_6 = 40         # 6张炸
    BOMB_7 = 41         # 7张炸
    BOMB_8 = 42         # 8张炸
    BOMB_9 = 43         # 9张炸
    BOMB_10 = 44        # 10+炸
    KING_BOMB = 100     # 四大天王

class PatternRecognizer:
    @staticmethod
    def get_legal_moves(hand: List[Card]) -> List[Dict]:
        """
        生成当前手牌所有合法的出牌组合
        """
        moves = []
        
        # 1. 预处理：分离 红桃2 和 普通牌
        wild_cards = [c for c in hand if c.is_wild]
        natural_cards = [c for c in hand if not c.is_wild]
        wild_count = len(wild_cards)
        
        # 统计点数: { Rank.R3: [Card, Card], ... }
        rank_map = defaultdict(list)
        for c in natural_cards:
            rank_map[c.rank].append(c)

        # 统计花色+点数，用于正确枚举同花顺（避免“每个点数取第一张”导致错过同花顺）
        suit_rank_map = defaultdict(lambda: defaultdict(list))  # suit -> rank -> [Card...]
        for c in natural_cards:
            suit_rank_map[c.suit][c.rank].append(c)
        
        unique_ranks = sorted(rank_map.keys())

        # --- A. 识别四大天王 ---
        kings = rank_map.get(Rank.R_BIG, []) + rank_map.get(Rank.R_SMALL, [])
        if len(kings) == 4:
            moves.append(PatternRecognizer._make_move(CardType.KING_BOMB, 999, kings, "四大天王"))

        def format_rank_label(rank: Rank) -> str:
            label_map = {
                Rank.RJ: 'J',
                Rank.RQ: 'Q',
                Rank.RK: 'K',
                Rank.RA: 'A',
                Rank.R2: '2',
            }
            if rank == Rank.R_SMALL:
                return '小王'
            if rank == Rank.R_BIG:
                return '大王'
            return label_map.get(rank, str(rank.value))

        # --- B. 识别炸弹 (4张及以上) ---
        for r in unique_ranks:
            if r >= Rank.R_SMALL: continue # 王不组普通炸
            
            naturals = rank_map[r]
            count = len(naturals)
            
            # 遍历使用 0 到 wild_count 个赖子
            # 修复：允许用户选择使用多少个赖子，而不是强制全部使用
            for w in range(wild_count + 1):
                # 如果不带赖子且自然牌少于4张，跳过
                if w == 0 and count < 4: continue
                # 如果总张数少于4张，跳过
                if count + w < 4: continue
                
                combo = naturals + wild_cards[:w]
                length = len(combo)
                
                # 确定炸弹类型
                b_type = CardType.BOMB_4
                if length == 5: b_type = CardType.BOMB_5
                elif length == 6: b_type = CardType.BOMB_6
                elif length == 7: b_type = CardType.BOMB_7
                elif length == 8: b_type = CardType.BOMB_8
                elif length == 9: b_type = CardType.BOMB_9
                elif length >= 10: b_type = CardType.BOMB_10
                
                label = format_rank_label(r)
                desc = f"{length}张{label}"
                if w > 0: desc += " (含赖子)"
                
                moves.append(PatternRecognizer._make_move(b_type, r, combo, desc))

        # --- C. 识别顺子/同花顺 (严格5张) ---
        # 说明：旧实现“每个点数取第一张牌”会在同点数多花色时错过同花顺。
        # 新实现：先按花色枚举同花顺，再枚举普通顺子。
        for start in range(Rank.R3, Rank.RA - 3):  # 3 到 10
            end = start + 4
            target_ranks = range(start, end + 1)

            # 1) 同花顺：按花色尝试拼齐5连（缺的用赖子补）
            emitted_all_wild_sf = False
            for suit in Suit:
                needed_wilds = 0
                combo_natural = []
                for tr in target_ranks:
                    if tr in suit_rank_map[suit]:
                        combo_natural.append(suit_rank_map[suit][tr][0])
                    else:
                        needed_wilds += 1

                if needed_wilds <= wild_count:
                    used_wilds = wild_cards[:needed_wilds]
                    final_cards = combo_natural + used_wilds

                    # 全赖子时会对每个花色都成立：只发一次避免重复
                    if len(combo_natural) == 0:
                        if emitted_all_wild_sf:
                            continue
                        emitted_all_wild_sf = True

                    moves.append(
                        PatternRecognizer._make_move(
                            CardType.STRAIGHT_FLUSH,
                            end,
                            final_cards,
                            f"同花顺 {start}-{end}"
                        )
                    )

            # 2) 普通顺子：不限定花色（任意花色凑齐5连，缺的用赖子补）
            needed_wilds = 0
            combo_natural = []
            for tr in target_ranks:
                if tr in rank_map:
                    combo_natural.append(rank_map[tr][0])
                else:
                    needed_wilds += 1

            if needed_wilds <= wild_count:
                used_wilds = wild_cards[:needed_wilds]
                final_cards = combo_natural + used_wilds
                moves.append(
                    PatternRecognizer._make_move(
                        CardType.STRAIGHT,
                        end,
                        final_cards,
                        f"顺子 {start}-{end}"
                    )
                )

        # --- C_Special. 识别特殊顺子 (A-2-3-4-5, 2-3-4-5-6) ---
        # 规则：A-2-3-4-5 (最小顺子, 虚拟Rank=5), 2-3-4-5-6 (次小顺子, 虚拟Rank=6)
        special_straights = [
            ([Rank.RA, Rank.R2, Rank.R3, Rank.R4, Rank.R5], 5, "A-5"),
            ([Rank.R2, Rank.R3, Rank.R4, Rank.R5, Rank.R6], 6, "2-6")
        ]
        
        for target_ranks, end_rank, name_suffix in special_straights:
            # 1) 同花顺：按花色
            emitted_all_wild_sf = False
            for suit in Suit:
                needed_wilds = 0
                combo_natural = []
                for tr in target_ranks:
                    if tr in suit_rank_map[suit]:
                        combo_natural.append(suit_rank_map[suit][tr][0])
                    else:
                        needed_wilds += 1

                if needed_wilds <= wild_count:
                    used_wilds = wild_cards[:needed_wilds]
                    final_cards = combo_natural + used_wilds

                    if len(combo_natural) == 0:
                        if emitted_all_wild_sf:
                            continue
                        emitted_all_wild_sf = True

                    moves.append(
                        PatternRecognizer._make_move(
                            CardType.STRAIGHT_FLUSH,
                            end_rank,
                            final_cards,
                            f"同花顺 {name_suffix}"
                        )
                    )

            # 2) 普通顺子：不限定花色
            needed_wilds = 0
            combo_natural = []
            for tr in target_ranks:
                if tr in rank_map:
                    combo_natural.append(rank_map[tr][0])
                else:
                    needed_wilds += 1

            if needed_wilds <= wild_count:
                used_wilds = wild_cards[:needed_wilds]
                final_cards = combo_natural + used_wilds
                moves.append(
                    PatternRecognizer._make_move(
                        CardType.STRAIGHT,
                        end_rank,
                        final_cards,
                        f"顺子 {name_suffix}"
                    )
                )

        # --- C2. 识别连对 (3连对) ---
        # 范围 3(3) - A(14)
        for start in range(Rank.R3, Rank.RA - 1): # 3 到 Q
            target_ranks = [start, start+1, start+2]
            needed_wilds = 0
            combo_natural = []
            
            for tr in target_ranks:
                count = len(rank_map[tr])
                if count >= 2:
                    combo_natural.extend(rank_map[tr][:2])
                elif count == 1:
                    combo_natural.append(rank_map[tr][0])
                    needed_wilds += 1
                else:
                    needed_wilds += 2
            
            if needed_wilds <= wild_count:
                used_wilds = wild_cards[:needed_wilds]
                final_cards = combo_natural + used_wilds
                moves.append(PatternRecognizer._make_move(
                    CardType.CONSECUTIVE_PAIRS, start+2, final_cards, f"连对 {start}-{start+2}"
                ))

        # --- C3. 识别钢板 (2连三张) ---
        # 范围 3(3) - A(14)
        for start in range(Rank.R3, Rank.RA): # 3 到 K
            target_ranks = [start, start+1]
            needed_wilds = 0
            combo_natural = []
            
            for tr in target_ranks:
                count = len(rank_map[tr])
                if count >= 3:
                    combo_natural.extend(rank_map[tr][:3])
                else:
                    combo_natural.extend(rank_map[tr])
                    needed_wilds += (3 - count)
            
            if needed_wilds <= wild_count:
                used_wilds = wild_cards[:needed_wilds]
                final_cards = combo_natural + used_wilds
                moves.append(PatternRecognizer._make_move(
                    CardType.CONSECUTIVE_TRIPLETS, start+1, final_cards, f"钢板 {start}-{start+1}"
                ))

        # --- C4. 识别三张 (不带牌) ---
        for r in unique_ranks:
            if r >= Rank.R_SMALL: continue
            label = format_rank_label(r)
            count = len(rank_map[r])
            if count >= 3:
                moves.append(PatternRecognizer._make_move(CardType.TRIPLET, r, rank_map[r][:3], f"三张{label}"))
            elif count == 2 and wild_count >= 1:
                moves.append(PatternRecognizer._make_move(CardType.TRIPLET, r, rank_map[r] + wild_cards[:1], f"三张{label} (赖子)"))

        # --- D. 识别三带二 ---
        # 简化版：只找自然三张或自然对子，不让赖子太累
        triplets = [] # (rank, cards)
        pairs = []    # (rank, cards)
        
        # 1. 找三张 (含赖子补)
        for r in unique_ranks:
            if r >= Rank.R_SMALL: continue
            c_len = len(rank_map[r])
            if c_len >= 3:
                triplets.append((r, rank_map[r][:3]))
            elif c_len == 2 and wild_count >= 1:
                triplets.append((r, rank_map[r] + wild_cards[:1]))
        
        # 2. 找对子 (含赖子补, 修复: 允许用赖子补对子以组成三带二)
        for r in unique_ranks:
            if r >= Rank.R_SMALL: continue
            c_len = len(rank_map[r])
            if c_len >= 2:
                # 天然对
                pairs.append((r, rank_map[r][:2]))
            elif c_len == 1 and wild_count >= 1:
                # 赖子补对 (只取第一个赖子尝试，后续靠ID去重逻辑来验证是否与三张冲突)
                pairs.append((r, rank_map[r] + wild_cards[:1]))
        
        # 3. 拼装
        for t_r, t_c in triplets:
            t_label = format_rank_label(t_r)
            for p_r, p_c in pairs:
                if t_r == p_r: continue
                p_label = format_rank_label(p_r)
                # 简单的ID查重，防止赖子被复用
                all_ids = set(c.id for c in t_c + p_c)
                if len(all_ids) == 5:
                    moves.append(PatternRecognizer._make_move(
                        CardType.TRIPLET_PAIR, t_r, t_c + p_c, f"三{t_label}带对{p_label}"
                    ))

        # --- E. 识别对子 (含赖子) ---
        for r in unique_ranks:
            label = format_rank_label(r)
            # 纯对
            if len(rank_map[r]) >= 2:
                moves.append(PatternRecognizer._make_move(CardType.PAIR, r, rank_map[r][:2], f"对{label}"))
            # 赖子凑对：红桃2(逢人配)严禁与王牌(大王/小王)配对
            elif len(rank_map[r]) == 1 and wild_count >= 1 and r < Rank.R_SMALL:
                moves.append(PatternRecognizer._make_move(CardType.PAIR, r, [rank_map[r][0], wild_cards[0]], f"对{label} (赖子)"))
        
        # --- F. 单张 ---
        def format_card_label(card: Card) -> str:
            """生成友好的单张牌标签，去除花色符号和JK前缀"""
            label = format_rank_label(card.rank)
            return label

        for r in unique_ranks:
            for card in rank_map[r]:
                moves.append(PatternRecognizer._make_move(
                    CardType.SINGLE,
                    r,
                    [card],
                    f"一张{format_card_label(card)}"
                ))
        
        # 单独出红桃2（逢人配）
        for wild in wild_cards:
            moves.append(PatternRecognizer._make_move(
                CardType.SINGLE,
                Rank.R2,
                [wild],
                f"一张{format_card_label(wild)}"
            ))

        return moves

    @staticmethod
    def _make_move(m_type, rank, cards, desc):
        """辅助函数：构建标准返回格式"""
        return {
            "type": m_type,      # 枚举值
            "rank": rank,        # 比较点数
            "cards": cards,      # Card对象列表
            "card_ids": [c.id for c in cards], # ID列表(给前端/LLM)
            "desc": desc         # 描述文本
        }
    
class Comparator:
    @staticmethod
    def get_type_weight(m_type: int, length: int = 0) -> int:
        """
        计算牌型绝对权重，用于跨牌型比较
        例如：四大天王 > 8张炸 > 7张炸 > 6张炸 > 同花顺 > 5张炸 > 4张炸
        """
        if m_type == CardType.KING_BOMB:
            return 999

        # 同花顺：>5炸, <6炸
        if m_type == CardType.STRAIGHT_FLUSH:
            return 50

        # 炸弹系列：按张数定权重
        if m_type >= CardType.BOMB_4 and m_type <= CardType.BOMB_10:
            # 优先使用真实张数
            if length >= 8:
                return 80 + length
            if length == 7:
                return 70
            if length == 6:
                return 60
            if length == 5:
                return 40
            if length == 4:
                return 30
            # 兜底：如果 length 没传对，用 type 估算
            if m_type == CardType.BOMB_4:
                return 30
            if m_type == CardType.BOMB_5:
                return 40
            if m_type == CardType.BOMB_6:
                return 60
            if m_type == CardType.BOMB_7:
                return 70
            # 8张及以上
            return 80 + (m_type - CardType.BOMB_8 + 8)

        # 普通牌型权重最低
        return 1

    @staticmethod
    def can_beat(last_move: Dict, new_move: Dict) -> bool:
        """
        判断 new_move 是否能压制 last_move
        """
        if not last_move: return True # 如果是首发，当然可以出
        if new_move['type'] == CardType.INVALID: return False
        if new_move['type'] == 0: return False # PASS

        last_type = last_move['type']
        new_type = new_move['type']
        
        # 获取卡牌数量（用于判断炸弹张数）
        # 兼容某些路径只携带 card_ids 的情况
        last_cards = last_move.get('cards') or []
        new_cards = new_move.get('cards') or []
        last_len = len(last_cards) if last_cards else len(last_move.get('card_ids') or [])
        new_len = len(new_cards) if new_cards else len(new_move.get('card_ids') or [])

        # 显式规则：
        # 四大天王 > 8张炸弹 > 7张炸弹 > 6张炸弹 > 同花顺 > 5张炸弹 > 4张炸弹 > 普通牌型
        # 这里按“长度”判断炸弹张数，避免未来 type 值被改坏造成回归。
        last_is_bomb = int(last_type) >= int(CardType.BOMB_4) and int(last_type) != int(CardType.STRAIGHT_FLUSH) and int(last_type) != int(CardType.KING_BOMB)
        new_is_bomb = int(new_type) >= int(CardType.BOMB_4) and int(new_type) != int(CardType.STRAIGHT_FLUSH) and int(new_type) != int(CardType.KING_BOMB)

        if int(new_type) == int(CardType.STRAIGHT_FLUSH) and last_is_bomb:
            if last_len >= 6:
                return False
            if last_len in (4, 5):
                return True

        if int(last_type) == int(CardType.STRAIGHT_FLUSH) and new_is_bomb:
            if new_len >= 6:
                return True
            if new_len in (4, 5):
                return False

        # 1. 计算绝对权重
        weight_last = Comparator.get_type_weight(last_type, last_len)
        weight_new = Comparator.get_type_weight(new_type, new_len)

        # 2. 权重不同：直接比权重 (例如 炸弹 vs 单张，或 6炸 vs 5炸)
        # 注意：如果 weight_new > 1 (是炸弹) 且 weight_last == 1 (是普通牌)，则炸弹必胜
        if weight_new > 1 and weight_last == 1:
            return True
            
        if weight_new > weight_last:
            return True
        if weight_new < weight_last:
            return False

        # 3. 权重相同：必须是同一种类逻辑才能比
        # 情况 A: 都是普通牌型 (权重=1) -> 必须类型一致且张数一致
        if weight_new == 1:
            if last_type != new_type: return False
            if last_len != new_len: return False
            return new_move['rank'] > last_move['rank']
            
        # 情况 B: 都是炸弹/同花顺 (权重 > 1)
        # 此时权重相同意味着：都是同花顺，或者都是5张炸，或者都是6张炸...
        # 直接比点数
        return new_move['rank'] > last_move['rank']

    @staticmethod
    def get_beat_error(last_move: Dict, new_move: Dict) -> str:
        """
        获取无法压制的具体原因
        Returns: None if can beat, generic error string otherwise
        """
        if not last_move: 
            return None # 任意牌首发
            
        if new_move['type'] == CardType.INVALID or new_move['type'] == 0: 
            return "无效的牌型"

        last_type = last_move['type']
        new_type = new_move['type']
        
        last_cards = last_move.get('cards') or []
        new_cards = new_move.get('cards') or []
        last_len = len(last_cards) if last_cards else len(last_move.get('card_ids') or [])
        new_len = len(new_cards) if new_cards else len(new_move.get('card_ids') or [])

        # 炸弹判断逻辑重用
        last_is_bomb = int(last_type) >= int(CardType.BOMB_4) and int(last_type) != int(CardType.STRAIGHT_FLUSH) and int(last_type) != int(CardType.KING_BOMB)
        new_is_bomb = int(new_type) >= int(CardType.BOMB_4) and int(new_type) != int(CardType.STRAIGHT_FLUSH) and int(new_type) != int(CardType.KING_BOMB)

        # 1. 权重比较
        weight_last = Comparator.get_type_weight(last_type, last_len)
        weight_new = Comparator.get_type_weight(new_type, new_len)

        # 特殊情况：同花顺 vs 炸弹
        if int(new_type) == int(CardType.STRAIGHT_FLUSH) and last_is_bomb:
            if last_len >= 6: return "同花顺管不上6张及以上炸弹"
        if int(last_type) == int(CardType.STRAIGHT_FLUSH) and new_is_bomb:
            if new_len < 6: return "普通炸弹管不上同花顺(需6张炸以上)"

        if weight_new > weight_last:
            # 特殊检查：如果 weight_last 是 1 (普通牌) 而 weight_new > 1 (炸弹)，则一定能管
            if weight_last == 1: return None
            return None # 权重压制
            
        if weight_new < weight_last:
            if weight_new == 1:
                return "普通牌型管不上炸弹/同花顺/天王炸"
            return "炸弹太小(张数或级别不够)"

        # 2. 权重相同
        # A. 普通牌型
        if weight_new == 1:
            if last_type != new_type:
                type_names = {1:"单张", 2:"对子", 3:"三张", 4:"三带二", 5:"顺子", 6:"连对", 7:"钢板"}
                t1 = type_names.get(last_type, str(last_type))
                t2 = type_names.get(new_type, str(new_type))
                return f"牌型不匹配，上家出的是: {t1}"
            
            if last_len != new_len:
                return f"张数不一致 (需{last_len}张)"
                
            if new_move['rank'] <= last_move['rank']:
                return "点数不够大"
                
            return None # 可以管

        # B. 都是炸弹/同花顺
        if new_move['rank'] <= last_move['rank']:
            return "点数不够大"

        return None
    
    @staticmethod
    def is_takeover_worthy(last_move: Dict, next_player_card_count: int = 99) -> bool:
        """
        判断队友的牌是否值得接风（让AI选择PASS等待首发）
        
        Args:
            last_move: 队友最后出的牌
            next_player_card_count: 下家剩余牌数（用于判断是否会被下家顺走）
        
        Returns:
            True: 值得接风（AI应该PASS）
            False: 不值得接风（AI应该跟牌争控）
        """
        if not last_move:
            return False
        
        move_type = last_move.get('type', 0)
        move_rank = last_move.get('rank', 0)
        move_len = len(last_move.get('cards', []))
        
        # 1. 下家剩余牌数过少（≤3张），必须拦截，不能接风
        #    因为下家很可能一手牌走掉
        if next_player_card_count <= 3:
            return False
        
        # 2. 判断队友的牌是否"足够大"，值得接风
        
        # 2.1 炸弹（任何张数）-> 值得接风
        if move_type >= CardType.BOMB_4:
            return True
        
        # 2.2 同花顺 -> 值得接风
        if move_type == CardType.STRAIGHT_FLUSH:
            return True
        
        # 2.3 钢板（2连三张）-> 值得接风
        if move_type == CardType.CONSECUTIVE_TRIPLETS:
            return True
        
        # 2.4 大三带二（三张的点数 >= K）-> 值得接风
        if move_type == CardType.TRIPLET_PAIR:
            # Rank.RK = 13, Rank.RA = 14, Rank.R2 = 15
            if move_rank >= 13:  # K及以上
                return True
        
        # 2.5 大对子（对A/对2/对王）-> 值得接风
        if move_type == CardType.PAIR:
            # Rank.RA = 14, Rank.R2 = 15, Rank.R_SMALL = 20, Rank.R_BIG = 21
            if move_rank >= 14:
                return True
        
        # 2.6 大三张（三张A/三张2）-> 值得接风
        if move_type == CardType.TRIPLET:
            if move_rank >= 14:
                return True
        
        # 2.7 大单张（2/王）-> 值得接风
        if move_type == CardType.SINGLE:
            # Rank.R2 = 15, Rank.R_SMALL = 20, Rank.R_BIG = 21
            if move_rank >= 15:
                return True
        
        # 2.8 大连对（最高牌 >= K）-> 值得接风
        if move_type == CardType.CONSECUTIVE_PAIRS:
            if move_rank >= 13:
                return True
        
        # 2.9 大顺子（最高牌 >= K）-> 值得接风
        if move_type == CardType.STRAIGHT:
            if move_rank >= 13:
                return True
        
        # 其他情况：不值得接风
        return False