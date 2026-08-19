# game/backend/tactics.py

from __future__ import annotations
from typing import Optional, Tuple, List, Union, Dict, Set

# 3.2 TACTICS_DB 已外置为 tactics_data.json（可由非程序员编辑策略）。
# 启动即加载；该文件随仓库发布，缺失视为部署错误。
import json as _json
from pathlib import Path as _Path

_TACTICS_JSON_PATH = _Path(__file__).resolve().parent / "tactics_data.json"
try:
    with open(_TACTICS_JSON_PATH, "r", encoding="utf-8") as _f:
        TACTICS_DB = _json.load(_f)
except (OSError, _json.JSONDecodeError) as _e:  # pragma: no cover
    raise RuntimeError(f"无法加载策略数据库 {_TACTICS_JSON_PATH}: {_e}") from _e


def calculate_hand_optimization(hand_cards_list, return_all: bool = False, two_weight: float = 1.0):
    """
    硬编码计算两种组牌方案的 P 值
    P = 轮次数 - (炸弹数 + 同花顺数*2)
    """
    from collections import Counter, defaultdict
    from copy import deepcopy

    # 1. 预处理手牌
    rank_map = {
        '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
        'J': 11, 'Q': 12, 'K': 13, 'A': 14, '2': 15, '小王': 20, '大王': 21,
        'B': 20, 'R': 21 # 兼容简写
    }
    value_to_label = {v: k for k, v in rank_map.items() if k not in ['B', 'R']}
    
    ranks = []
    wild_count = 0
    rank_suits = defaultdict(list)
    
    for card in hand_cards_list:
        # 优先检测红桃2 (H15)
        # 格式: H15-0, H15-1, 或 ♥2, H2
        if card.startswith('H15') or '♥2' in card or 'H2' in card:
            wild_count += 1
            continue

        # 兼容大小王字符串格式：JK大王 / JK小王
        if isinstance(card, str) and 'JK' in card:
            if '大王' in card:
                ranks.append(21)
                rank_suits[21].append('J')
                continue
            if '小王' in card:
                ranks.append(20)
                rank_suits[20].append('J')
                continue

        # 解析牌面，例如 'H3-0' -> rank 3
        # 格式通常为: [Suit][RankValue]-[DeckIdx]
        # 例如: H3-0, S11-1, J20-0
        
        r_val = 0
        suit = 'X'
        try:
            # 尝试解析标准格式 SuitRank-Idx
            if '-' in card:
                prefix = card.split('-')[0] # H3
                rank_part = prefix[1:] # 3
                r_val = int(rank_part)
                suit = prefix[0]
            else:
                # 尝试解析旧格式或简化格式
                rank_str = card[1:] if len(card) > 1 else card
                if rank_str in rank_map:
                    r_val = rank_map[rank_str]
                elif rank_str == 'JK': 
                     r_val = 20 if '小' in card else 21
                else:
                    r_val = int(rank_str)
                
                # 提取花色
                if '♥' in card: suit = 'H'
                elif '♠' in card: suit = 'S'
                elif '♣' in card: suit = 'C'
                elif '♦' in card: suit = 'D'
                elif card[0] in ['H','S','C','D']: suit = card[0]
        except:
            continue
            
        ranks.append(r_val)
        rank_suits[r_val].append(suit)

    ranks.sort()
    
    def get_label(r):
        return value_to_label.get(r, str(r))

    # 辅助函数：有限枚举计算最优方案
    def evaluate_best_plan():
        # 有限枚举关键顺序与保护策略，扩大搜索但控制复杂度
        # 组合策略：炸弹优先/顺子优先 × 顺序变体 × 是否保护三张

        def run_simulation(mode, config, allow_wild_sf=False):
            # mode: 'bomb_first' or 'straight_first'
            # config: dict controlling extraction order/constraints
            
            current_ranks = ranks.copy()
            current_rank_suits = deepcopy(rank_suits)
            
            # Helper to sync counts from current_rank_suits
            def refresh_counts():
                c = Counter()
                for r, s_list in current_rank_suits.items():
                    c[r] = len(s_list)
                return c
            
            counts = refresh_counts()
            original_counts = counts.copy()
            current_wilds = wild_count
            
            bombs_found = [] # List of (score, desc, length, is_sf)
            plates_found = []
            consecutive_pairs_found = []
            straights_found = []
            triples_found = []
            pairs_found = []
            singles_found = []
            sf_found_count = 0
            excluded_two = 0
            excluded_sj = 0
            excluded_bj = 0
            split_bomb_ranks = set()
            
            turns = 0
            
            # Helper to remove cards
            def remove_cards(r, count):
                nonlocal current_wilds
                # Remove 'count' cards of rank 'r' from current_rank_suits
                removed = 0
                if r == 15 and counts[r] < count:
                    # 原本2不够，用current_wilds补
                    needed = count - counts[r]
                    if r in current_rank_suits:
                        while current_rank_suits[r]:
                            current_rank_suits[r].pop()
                    counts[r] = 0
                    current_wilds -= needed
                else:
                    if r in current_rank_suits:
                        while removed < count and current_rank_suits[r]:
                            current_rank_suits[r].pop()
                            removed += 1
                    counts[r] -= count

            def mark_split_bomb_rank(r: int):
                if original_counts.get(r, 0) >= 4:
                    split_bomb_ranks.add(r)

            # --- Step 0: 天王炸 (4 Jokers) - 绝对优先 ---
            # 检查是否有 2张小王(20) + 2张大王(21)
            if counts[20] == 2 and counts[21] == 2:
                bombs_found.append((1000, "天王炸", 4, False))
                remove_cards(20, 2)
                remove_cards(21, 2)
                turns += 1
            
            # 定义各步骤函数
            def step_straight_flushes(allow_wild_sf=False):
                nonlocal turns, sf_found_count, current_wilds
                # Check for 5 consecutive ranks with same suit
                # Suits: S, H, C, D
                # 【优化】：优先检测同花顺，避免被炸弹拆散
                suits_list = ['S', 'H', 'C', 'D']
                while True:
                    found_sf = False
                    sorted_keys = sorted([k for k, v in counts.items() if v > 0 and k < 15])
                    
                    candidates = []
                    # 特殊顺子：A-2-3-4-5
                    candidates.append([14, 15, 3, 4, 5])
                    # 特殊顺子：2-3-4-5-6
                    candidates.append([15, 3, 4, 5, 6])
                    
                    if len(sorted_keys) >= 5:
                        for i in range(len(sorted_keys) - 4):
                            start_rank = sorted_keys[i]
                            candidates.append([start_rank + j for j in range(5)])
                    
                    # 尝试所有候选顺子
                    for potential in candidates:
                        # 【改进】：支持红桃2补全
                        best_suit = None
                        best_priority = -1
                        is_using_wild = False
                        
                        for s in suits_list:
                            # 已经拥有的该花色的张数
                            available_count = sum(1 for r in potential if s in current_rank_suits.get(r, []))
                            
                            # 1. 检查是否为自然同花顺
                            if available_count == 5:
                                priority = 0
                                for r in potential:
                                    suit_count = current_rank_suits.get(r, []).count(s)
                                    if suit_count == 1 or counts.get(r, 0) < 4:
                                        priority += 10
                                    elif counts.get(r, 0) >= 6:
                                        priority -= 100
                                    elif counts.get(r, 0) == 4:
                                        priority += 5
                                    elif counts.get(r, 0) == 5:
                                        priority -= 10
                                
                                if priority > best_priority:
                                    best_priority = priority
                                    best_suit = s
                                    is_using_wild = False

                            # 2. 检查是否可以用红桃2补全
                            elif allow_wild_sf and current_wilds > 0 and available_count == 4:
                                # 找到缺失的那张牌的点数
                                missing_rank = [r for r in potential if s not in current_rank_suits.get(r, [])][0]
                                
                                priority = -50 
                                for r in potential:
                                    if r == missing_rank: continue
                                    suit_count = current_rank_suits.get(r, []).count(s)
                                    if suit_count == 1 or counts.get(r, 0) < 4:
                                        priority += 10
                                    elif counts.get(r, 0) >= 6:
                                        priority -= 100
                                
                                if priority + 40 > best_priority:
                                    best_priority = priority + 40
                                    best_suit = s
                                    is_using_wild = True
                        
                        if best_suit:
                            # Found SF!
                            if is_using_wild:
                                desc = "".join([get_label(x) for x in potential]) + f"({best_suit}同花+红桃2)"
                                current_wilds -= 1
                                # Remove 4 cards
                                for r in potential:
                                    if best_suit in current_rank_suits.get(r, []):
                                        current_rank_suits[r].remove(best_suit)
                                        counts[r] -= 1
                            else:
                                desc = "".join([get_label(x) for x in potential]) + f"({best_suit}同花顺)"
                                # Remove 5 cards
                                for r in potential:
                                    current_rank_suits[r].remove(best_suit)
                                    counts[r] -= 1
                            
                            bombs_found.append((600 + potential[0], desc, 5, True))
                            sf_found_count += 1
                            turns += 1
                            found_sf = True
                            break
                    if not found_sf: break

            def step_natural_bombs(protect_sf=False):
                nonlocal turns, current_wilds
                distinct_ranks = sorted(list(counts.keys()))
                for r in distinct_ranks:
                    # 王不组普通炸弹 (含红桃2升级路径)，红桃2严禁与王牌组合
                    if r >= 20:
                        continue
                    if counts[r] >= 4:
                        count = counts[r]

                        # 若规则允许红桃2参与炸弹：
                        # 1) 优先把“已有 6/7 炸”升级到 7/8 炸（提升控牌+翻倍），最多升到 8 炸。
                        if current_wilds > 0 and count >= 6:
                            # protect_sf=True 只处理 6+ 炸，这里与该策略一致。
                            use_w = min(current_wilds, max(0, 8 - count))
                            if use_w > 0:
                                current_wilds -= use_w
                                bomb_len = count + use_w
                                score = 600 + r
                                bombs_found.append((score, get_label(r) * count + f"+红桃2x{use_w}({bomb_len}炸)", bomb_len, False))
                                remove_cards(r, count)
                                turns += 1
                                continue

                        # 若规则允许红桃2参与炸弹：优先用1张红桃2把“5炸”升级为“6炸”提升控牌价值
                        upgraded = False
                        if current_wilds > 0 and count == 5:
                            current_wilds -= 1
                            score = 600 + r
                            bombs_found.append((score, get_label(r) * 5 + "+红桃2(6炸)", 6, False))
                            remove_cards(r, 5)
                            turns += 1
                            upgraded = True
                        if upgraded:
                            continue
                        
                        # 【优化】：如果protect_sf=True，优先保护可能的同花顺
                        # 只处理6张及以上的炸弹（这些绝对值得保留）
                        if protect_sf and count < 6:
                            continue
                        
                        score = 400 + r # 基础分
                        if count == 5: score = 500 + r
                        elif count >= 6: score = 600 + r
                        
                        bombs_found.append((score, get_label(r) * count, count, False))
                        remove_cards(r, count)
                        turns += 1

            def step_wild_bombs():
                nonlocal turns, current_wilds
                if current_wilds > 0:
                    # 优化：优先选择点数较大的三张配成炸弹（如 KKK+H2 > 333+H2）
                    distinct_ranks = sorted(list(counts.keys()), reverse=True)
                    for r in distinct_ranks:
                        # 严禁用红桃2配王牌组成炸弹 (r < 20 排除大小王)
                        if counts[r] == 3 and r < 20 and current_wilds > 0:
                            current_wilds -= 1
                            remove_cards(r, 3)
                            # 优化：红桃2配3张2组成炸弹时不享受炸弹优先权（分值为0），以便优先考虑其他减少轮次的组合
                            score = 400 + r
                            if r == 15: score = 0
                            bombs_found.append((score, get_label(r) * 3 + "+红桃2", 4, False))
                            turns += 1

            def step_plates():
                nonlocal turns
                sorted_keys = sorted([k for k, v in counts.items() if v >= 3 and k < 15])
                i = 0
                while i < len(sorted_keys) - 1:
                    r1 = sorted_keys[i]
                    r2 = sorted_keys[i+1]
                    # 保护6张及以上炸弹不被拆成钢板
                    if r2 == r1 + 1 and 3 <= counts[r1] < 6 and 3 <= counts[r2] < 6:
                        # 钢板非炸弹，不应过分保护。当需要使用其中三张控牌时不应犹豫
                        plates_found.append(get_label(r1) * 3 + get_label(r2) * 3 + "(非炸弹)")
                        if r1 == 15:
                            excluded_two += 3
                        if r2 == 15:
                            excluded_two += 3
                        mark_split_bomb_rank(r1)
                        mark_split_bomb_rank(r2)
                        remove_cards(r1, 3)
                        remove_cards(r2, 3)
                        turns += 1
                        if counts[r1] < 3: i += 1
                    else:
                        i += 1

            def step_consecutive_pairs(only_exact_pairs: bool = False):
                """Find and remove consecutive pairs (连对).

                Rule constraint (as configured here): 连对只能包含三组对子（共6张），不允许更长。

                Notes:
                - Excludes rank 2 and jokers (k < 15), consistent with straight logic.
                - Greedy: split any longer run into 3-pair chunks.
                - Heuristic: when there are enough *natural pairs* to cover all triples,
                  avoid consuming ranks with 3+ cards so as not to reduce potential
                  三带二数量（更少轮次）。
                """
                nonlocal turns, excluded_two

                while True:
                    if only_exact_pairs:
                        pair_ranks = sorted([k for k, v in counts.items() if v == 2 and k < 15])
                    else:
                        pair_ranks = sorted([k for k, v in counts.items() if v >= 2 and k < 15])

                    if len(pair_ranks) < 3:
                        break

                    # Build consecutive runs
                    runs = []
                    cur = []
                    prev = None
                    for r in pair_ranks:
                        if prev is None or r == prev + 1:
                            cur.append(r)
                        else:
                            if cur:
                                runs.append(cur)
                            cur = [r]
                        prev = r
                    if cur:
                        runs.append(cur)

                    made_any = False
                    for run in runs:
                        # Split into 3-pair chunks from the start
                        while len(run) >= 3:
                            chunk = run[:3]
                            run = run[3:]
                            for rr in chunk:
                                remove_cards(rr, 2)
                                if rr == 15:
                                    excluded_two += 2
                                mark_split_bomb_rank(rr)
                            consecutive_pairs_found.append("".join(get_label(rr) * 2 for rr in chunk))
                            turns += 1
                            made_any = True

                    if not made_any:
                        break

            def step_straights(only_exact_singles: bool = False):
                nonlocal turns, excluded_two
                
                # 1. 动态生成所有可能的 5 张顺子候选（含掼蛋特殊顺子）
                def get_all_straights(c_counts):
                    cands = []
                    # 掼蛋特殊顺子：A-2-3-4-5 (14,15,3,4,5), 2-3-4-5-6 (15,3,4,5,6)
                    for spec in [[14, 15, 3, 4, 5], [15, 3, 4, 5, 6]]:
                        if all(c_counts.get(r, 0) > 0 for r in spec):
                            cands.append(spec)
                    # 普通顺子 3-4-5-6-7 到 10-J-Q-K-A (14)
                    ks = sorted([k for k, v in c_counts.items() if v > 0 and k < 15])
                    if len(ks) >= 5:
                        for i in range(len(ks) - 4):
                            r = ks[i]
                            potential = [r + j for j in range(5)]
                            if all(c_counts.get(p, 0) > 0 for p in potential):
                                cands.append(potential)
                    return cands

                # 2. 评估当前路径的优劣 (目标：总轮数最少 > 保留大牌最多)
                def eval_straights_path(c_counts, path_len):
                    rem_t, rem_p, rem_s = 0, 0, 0
                    big_card_score = 0
                    for r, cnt in c_counts.items():
                        if cnt >= 3: rem_t += 1
                        elif cnt == 2: rem_p += 1
                        elif cnt == 1: 
                            rem_s += 1
                            # 权重：保留 2 和 A 的控制力价值
                            if r == 15: big_card_score += 1.5
                            elif r == 14: big_card_score += 1.0
                            elif r >= 11: big_card_score += 0.3 # JQK
                    # 返回可比较的元组：(-总轮次, 大牌分数)
                    return (-(path_len + rem_t + rem_p + rem_s), big_card_score)

                best_res = {"score": (-999, -999), "path": [], "final_counts": counts.copy()}
                memo = {}

                def solve_straights(curr_counts, path):
                    # 备忘录防止重复计算
                    state_key = tuple(sorted(curr_counts.items()))
                    if state_key in memo: return memo[state_key]

                    # 基础评估（作为“如果不继续出顺子”的保底方案）
                    current_score = eval_straights_path(curr_counts, len(path))
                    if current_score > best_res["score"]:
                        best_res["score"] = current_score
                        best_res["path"] = path
                        best_res["final_counts"] = curr_counts.copy()

                    cands = get_all_straights(curr_counts)
                    # 过滤规则：不拆散 6 张及以上炸弹，且根据 only_exact_singles 判定是否拆对子/三张
                    valid_cands = []
                    for cand in cands:
                        if all(curr_counts.get(r, 0) < 6 for r in cand):
                            if only_exact_singles and any(curr_counts.get(r, 0) >= 2 for r in cand):
                                continue
                            valid_cands.append(cand)

                    best_of_branch = current_score
                    for cand in valid_cands:
                        next_counts = curr_counts.copy()
                        for r in cand: next_counts[r] -= 1
                        res = solve_straights(next_counts, path + [cand])
                        if res > best_of_branch:
                            best_of_branch = res

                    memo[state_key] = best_of_branch
                    return best_of_branch

                # 开始搜索
                solve_straights(counts.copy(), [])

                # 3. 将搜索到的全局最优方案应用到实际状态中
                for st in best_res["path"]:
                    straights_found.append("".join([get_label(x) for x in st]))
                    turns += 1
                    for r in st:
                        remove_cards(r, 1) # 同步更新外部的 counts 和 current_rank_suits
                        if r == 15: excluded_two += 1
                        mark_split_bomb_rank(r)

            def step_wild_straights():
                nonlocal turns, current_wilds, excluded_two
                if current_wilds > 0:
                    while current_wilds > 0:
                        sorted_keys = sorted([k for k, v in counts.items() if v > 0 and k < 15])
                        if len(sorted_keys) < 4: break
                        found = False
                        for i in range(len(sorted_keys) - 3):
                            start_rank = sorted_keys[i]
                            potential = [start_rank + j for j in range(4)]
                            # 保护6张及以上炸弹不被拆成顺子
                            if all(counts.get(r, 0) > 0 and counts.get(r, 0) < 6 for r in potential):
                                for r in potential: remove_cards(r, 1)
                                current_wilds -= 1
                                excluded_two += 1
                                for r in potential:
                                    mark_split_bomb_rank(r)
                                straights_found.append("".join([get_label(x) for x in potential]) + "+红桃2")
                                turns += 1
                                found = True
                                break
                        if not found: break

            # 执行策略
            order = config.get("order", "plates_pairs_straights")
            protect_triples = bool(config.get("protect_triples", False))
            skip_plates = bool(config.get("skip_plates", False))

            def run_mid_steps():
                if order == "plates_pairs_straights":
                    if not skip_plates:
                        step_plates()
                    step_consecutive_pairs(only_exact_pairs=protect_triples)
                    step_straights(only_exact_singles=protect_triples)
                elif order == "plates_straights_pairs":
                    if not skip_plates:
                        step_plates()
                    step_straights(only_exact_singles=protect_triples)
                    step_consecutive_pairs(only_exact_pairs=protect_triples)
                elif order == "straights_pairs_plates":
                    step_straights(only_exact_singles=protect_triples)
                    step_consecutive_pairs(only_exact_pairs=protect_triples)
                    if not skip_plates:
                        step_plates()
                else:
                    if not skip_plates:
                        step_plates()
                    step_consecutive_pairs(only_exact_pairs=protect_triples)
                    step_straights(only_exact_singles=protect_triples)

            wild_mode = config.get("wild_mode", "bomb_then_straight")

            def run_wild_steps_before_mid():
                step_wild_bombs()
                step_wild_straights()

            def run_wild_steps_after_mid():
                step_wild_straights()
                step_wild_bombs()

            if mode == 'bomb_first':
                # 先提取6张及以上炸弹，再检测同花顺，最后处理4-5张炸弹
                step_natural_bombs(protect_sf=True)  # 只处理6张以上
                step_straight_flushes(allow_wild_sf=allow_wild_sf)  # 检测同花顺
                step_natural_bombs(protect_sf=False)  # 处理剩余的4-5张炸弹

                if wild_mode == "wild_first":
                    run_wild_steps_before_mid()
                    run_mid_steps()
                elif wild_mode == "wild_last":
                    run_mid_steps()
                    run_wild_steps_after_mid()
                elif wild_mode == "straight_then_bomb":
                    step_wild_straights()
                    run_mid_steps()
                    step_wild_bombs()
                else:
                    step_wild_bombs()
                    run_mid_steps()
                    step_wild_straights()
            else:
                # straight_first模式：同花顺绝对优先
                step_straight_flushes(allow_wild_sf=allow_wild_sf) # 同花顺优先于普通顺子

                if wild_mode == "wild_first":
                    run_wild_steps_before_mid()
                    run_mid_steps()
                    step_natural_bombs(protect_sf=False)  # 最后处理炸弹
                    step_wild_bombs()
                elif wild_mode == "wild_last":
                    run_mid_steps()
                    step_natural_bombs(protect_sf=False)  # 最后处理炸弹
                    run_wild_steps_after_mid()
                elif wild_mode == "straight_then_bomb":
                    step_wild_straights()
                    run_mid_steps()
                    step_natural_bombs(protect_sf=False)  # 最后处理炸弹
                    step_wild_bombs()
                else:
                    run_mid_steps()
                    step_wild_straights()
                    step_natural_bombs(protect_sf=False)  # 最后处理炸弹
                    step_wild_bombs()
            
            # 后续清理 (相同)
            # Wild -> Triple
            # 优化：优先把大对子变成三张（例如 77 AA + H2 -> AAA 77 优于 777 AA）
            # 所以使用 reverse=True，先遍历大牌
            triple_rank_sum = 0
            pair_rank_sum = 0
            if current_wilds > 0:
                for r in sorted(list(counts.keys()), reverse=True):
                    if counts[r] == 2 and r < 20 and current_wilds > 0:
                        current_wilds -= 1
                        remove_cards(r, 2)
                        triples_found.append(get_label(r) * 2 + "+红桃2")
                        triple_rank_sum += r

            # Wild -> Pair
            # 同样优先配大对子（严禁配王牌）
            if current_wilds > 0:
                for r in sorted(list(counts.keys()), reverse=True):
                    if counts[r] == 1 and r < 20 and current_wilds > 0:
                        current_wilds -= 1
                        remove_cards(r, 1)
                        pairs_found.append(get_label(r) + "+红桃2")
                        pair_rank_sum += r

            # Remaining
            small_singles_count = 0
            big_singles_count = 0
            
            # 【优化】：使用 reverse=True 先收集大牌，这样 t_copy[0] 就是最大的三张，优先凑成更强的三带二
            for r in sorted(counts.keys(), reverse=True):
                if counts[r] == 3: 
                    triples_found.append(get_label(r) * 3)
                    triple_rank_sum += r
                elif counts[r] == 2: 
                    pairs_found.append(get_label(r) * 2)
                    pair_rank_sum += r
                elif counts[r] == 1: 
                    singles_found.append(get_label(r))
                    if r < 14: # 3-K (A以下孤张)
                        small_singles_count += 1
                    elif r >= 14: # A, 2, Jokers (大牌单张)
                        big_singles_count += 1
            
            if current_wilds > 0:
                singles_found.append(f"红桃2 x{current_wilds}")
                # 红桃2算大牌单张
                big_singles_count += current_wilds

            # 末尾检查：若原本是4+炸弹但被拆散（仍有剩余），加惩罚
            for r, c in counts.items():
                if c > 0 and original_counts.get(r, 0) >= 4:
                    split_bomb_ranks.add(r)

            # Calc Turns
            raw_turns = len(triples_found) + len(pairs_found) + len(singles_found)
            t_copy = triples_found.copy()
            p_copy = pairs_found.copy()
            s_copy = singles_found.copy()
            combos = []
            def _label_rank_value(label: str) -> int:
                try:
                    base = label.split("+", 1)[0]
                    if "小王" in base:
                        return 20
                    if "大王" in base:
                        return 21
                    if base == "红桃2":
                        return 15
                    if base == "10":
                        return 10
                    if base in rank_map:
                        return rank_map[base]
                    if base and base[0] in rank_map:
                        return rank_map[base[0]]
                    return int(base)
                except Exception:
                    return 99

            while t_copy and p_copy:
                t = t_copy.pop(0)

                def _pair_penalty(label: str) -> tuple:
                    use_sj = "小王" in label
                    use_bj = "大王" in label
                    use_two = "2" in label
                    if "+红桃2" in label:
                        use_two = label.count("2") > 1
                    penalty = 0
                    if use_bj:
                        penalty += 3
                    if use_sj:
                        penalty += 2
                    if use_two:
                        penalty += 1
                    return (penalty, _label_rank_value(label))

                p_copy.sort(key=_pair_penalty)
                p = p_copy.pop(0)
                combos.append(f"{t}{p}")
                raw_turns -= 1
                # 若2/王被用于三带二，则不计入控牌能力得分
                if "小王" in p:
                    excluded_sj += p.count("小王")
                if "大王" in p:
                    excluded_bj += p.count("大王")
                if "2" in p:
                    count_two = p.count("2")
                    # 若为"2+红桃2"，只扣除真实2，红桃2已不计入剩余two_count
                    if "+红桃2" in p:
                        count_two -= 1
                    if count_two > 0:
                        excluded_two += count_two
            
            penalty_turns = len(split_bomb_ranks) * 1
            total_turns = turns + raw_turns + penalty_turns
            bomb_score = sum(b[0] for b in bombs_found)
            effective_bombs = [b for b in bombs_found if b[0] > 0]
            effective_bomb_count = len(effective_bombs)

            bomb_4 = 0
            bomb_5 = 0
            bomb_6_plus = 0
            # 备注：红桃2配炸弹（即使score=0）也应计入炸弹控牌数量
            for _, _, blen, is_sf in bombs_found:
                if is_sf:
                    continue
                if blen >= 6:
                    bomb_6_plus += 1
                elif blen == 5:
                    bomb_5 += 1
                elif blen == 4:
                    bomb_4 += 1

            two_count = counts.get(15, 0)
            small_joker_count = counts.get(20, 0)
            big_joker_count = counts.get(21, 0)
            if current_wilds > 0:
                # 红桃2视作2
                two_count += current_wilds

            # 若2/王用于三带二/钢板/连对/顺子，则不计控牌得分（钢板/连对/顺子本身不会包含2/王）
            if excluded_two or excluded_sj or excluded_bj:
                two_count = max(0, two_count - excluded_two)
                small_joker_count = max(0, small_joker_count - excluded_sj)
                big_joker_count = max(0, big_joker_count - excluded_bj)
            
            # Construct details（按从小到大排列：散牌→对子→三张→三带→顺子→连对→钢板→炸弹，
            # 便于 LLM 理解这是牌型组织方案而非「按顺序出牌」，避免误把第一项（同花顺/大牌）当首选）
            detail_parts = []
            if s_copy: detail_parts.append(f"散牌:[{','.join(s_copy)}]")
            if p_copy: detail_parts.append(f"对子:[{','.join(p_copy)}]")
            if t_copy: detail_parts.append(f"三张:[{','.join(t_copy)}]")
            if combos: detail_parts.append(f"三带:[{','.join(combos)}]")
            if straights_found: detail_parts.append(f"顺子:[{','.join(straights_found)}]")
            if consecutive_pairs_found: detail_parts.append(f"连对:[{','.join(consecutive_pairs_found)}]")
            if plates_found: detail_parts.append(f"钢板:[{','.join(plates_found)}]")
            b_descs = [b[1] for b in bombs_found]
            if b_descs: detail_parts.append(f"炸弹:[{','.join(b_descs)}]")
            
            return {
                "turns": total_turns,
                "bomb_score": bomb_score,
                "bomb_count": len(bombs_found),
                "effective_bomb_count": effective_bomb_count,
                "sf_count": sf_found_count,
                "small_singles": small_singles_count,
                "big_singles": big_singles_count,
                "bomb_4": bomb_4,
                "bomb_5": bomb_5,
                "bomb_6_plus": bomb_6_plus,
                "two_count": two_count,
                "small_joker_count": small_joker_count,
                "big_joker_count": big_joker_count,
                "bomb_rank_sum": sum(max(0, b[0] - 400) for b in bombs_found if not b[3]),
                "triple_rank_sum": triple_rank_sum,
                "pair_rank_sum": pair_rank_sum,
                "details": ", ".join(detail_parts)
            }

        configs = [
            {"order": "plates_pairs_straights", "protect_triples": False, "wild_mode": "bomb_then_straight"},
            {"order": "plates_pairs_straights", "protect_triples": True, "wild_mode": "bomb_then_straight"},
            {"order": "plates_straights_pairs", "protect_triples": False, "wild_mode": "bomb_then_straight"},
            {"order": "plates_straights_pairs", "protect_triples": True, "wild_mode": "bomb_then_straight"},
            {"order": "straights_pairs_plates", "protect_triples": False, "wild_mode": "bomb_then_straight"},
            {"order": "straights_pairs_plates", "protect_triples": True, "wild_mode": "bomb_then_straight"},
            # 允许“拆钢板拼顺子”的分支：跳过钢板组牌
            {"order": "straights_pairs_plates", "protect_triples": False, "skip_plates": True, "wild_mode": "bomb_then_straight"},
            {"order": "straights_pairs_plates", "protect_triples": True, "skip_plates": True, "wild_mode": "bomb_then_straight"},
            # 红桃2分配搜索：优先顺子后炸弹 / 先用红桃2再中段 / 最后再用红桃2
            {"order": "plates_pairs_straights", "protect_triples": False, "wild_mode": "straight_then_bomb"},
            {"order": "plates_straights_pairs", "protect_triples": False, "wild_mode": "straight_then_bomb"},
            {"order": "straights_pairs_plates", "protect_triples": False, "wild_mode": "straight_then_bomb"},
            {"order": "plates_pairs_straights", "protect_triples": False, "wild_mode": "wild_first"},
            {"order": "plates_straights_pairs", "protect_triples": False, "wild_mode": "wild_first"},
            {"order": "straights_pairs_plates", "protect_triples": False, "wild_mode": "wild_first"},
            {"order": "plates_pairs_straights", "protect_triples": False, "wild_mode": "wild_last"},
            {"order": "plates_straights_pairs", "protect_triples": False, "wild_mode": "wild_last"},
            {"order": "straights_pairs_plates", "protect_triples": False, "wild_mode": "wild_last"},
        ]

        results_with_sf = []
        results_no_sf = []
        for cfg in configs:
            results_with_sf.append(run_simulation('bomb_first', cfg, allow_wild_sf=True))
            results_with_sf.append(run_simulation('straight_first', cfg, allow_wild_sf=True))
            
            results_no_sf.append(run_simulation('bomb_first', cfg, allow_wild_sf=False))
            results_no_sf.append(run_simulation('straight_first', cfg, allow_wild_sf=False))

        # 分别找出两种路径下的最优方案
        best_with_sf = results_with_sf[0]
        for candidate in results_with_sf[1:]:
            best_with_sf = _pick_min_group_score(best_with_sf, candidate, two_weight)
            
        best_no_sf = results_no_sf[0]
        for candidate in results_no_sf[1:]:
            best_no_sf = _pick_min_group_score(best_no_sf, candidate, two_weight)
            
        # 核心对比逻辑：
        # 如果 best_with_sf 的 sf_count 更多，说明确实用了红桃2配同花顺（或者自然多了SF）
        # 我们比较两者的 turns 差异
        turn_diff = best_with_sf["turns"] - best_no_sf["turns"]
        
        # 只有在 best_with_sf 确实产生了更多同花顺的情况下才需要对比
        if best_with_sf["sf_count"] > best_no_sf["sf_count"]:
            if turn_diff >= 2:
                # 代价过大，放弃 SF 方案
                final_best = best_no_sf
                final_best["rh2_sf_note"] = f"放弃红桃2补全同花顺方案（因轮次增加 {turn_diff} 轮）"
            else:
                # 代价在可接受范围内 (<=1轮)
                final_best = best_with_sf
                final_best["rh2_sf_note"] = f"已使用红桃2补全同花顺（轮次仅增加 {turn_diff} 轮，效率可接受）"
        else:
            # 两种路径结果一致，或者不用配也能出SF
            final_best = best_with_sf
            final_best["rh2_sf_note"] = None

        return [final_best]

    def _control_score(res, two_weight_value: float) -> float:
        # 基础控牌分 (由牌型数量决定)
        base_control = (
            (res.get("sf_count", 0) * 2.5)
            + (res.get("bomb_6_plus", 0) * 3.0)
            + (res.get("bomb_5", 0) * 2.2)
            + (res.get("bomb_4", 0) * 2.0)
            + (res.get("two_count", 0) * float(two_weight_value))
            + (res.get("small_joker_count", 0) * 1.2)
            + (res.get("big_joker_count", 0) * 1.5)
        )
        # 强度加成 (由牌的面值决定，作为同数量下的优选依据)
        # A=14, K=13, 3=3... 给予较小权重
        rank_power = (
            (res.get("bomb_rank_sum", 0) * 0.05) +
            (res.get("triple_rank_sum", 0) * 0.02) +
            (res.get("pair_rank_sum", 0) * 0.01)
        )
        return base_control + rank_power

    def _group_score(res, two_weight_value: float) -> float:
        return (
            float(res.get("turns", 0))
            - (_control_score(res, two_weight_value) / 2.0)
            + float(res.get("small_singles", 0))
        )

    def _pick_min_group_score(a, b, two_weight_value: float):
        sa = _group_score(a, two_weight_value)
        sb = _group_score(b, two_weight_value)
        if abs(sa - sb) > 1e-9:
            return a if sa < sb else b
        # tie-breakers: fewer turns, fewer small singles, higher control
        if a["turns"] != b["turns"]:
            return a if a["turns"] < b["turns"] else b
        if a["small_singles"] != b["small_singles"]:
            return a if a["small_singles"] < b["small_singles"] else b
        if a.get("bomb_count", 0) != b.get("bomb_count", 0):
            return a if a.get("bomb_count", 0) > b.get("bomb_count", 0) else b
        ca = _control_score(a, two_weight_value)
        cb = _control_score(b, two_weight_value)
        return a if ca >= cb else b

    results = evaluate_best_plan()
    best_min_turns = results[0]

    def _with_control_meta(res, two_weight_value: float) -> dict:
        meta = dict(res)
        meta["two_weight"] = float(two_weight_value)
        meta["control_score"] = _control_score(res, two_weight_value)
        
        # Calculate breakdown for logging
        sf_score = (res.get("sf_count", 0) * 2.5)
        b6_score = (res.get("bomb_6_plus", 0) * 3.0)
        b5_score = (res.get("bomb_5", 0) * 2.2)
        b4_score = (res.get("bomb_4", 0) * 2.0)
        two_score = (res.get("two_count", 0) * float(two_weight_value))
        sj_score = (res.get("small_joker_count", 0) * 1.2)
        bj_score = (res.get("big_joker_count", 0) * 1.5)
        
        meta["control_score_breakdown"] = (
            f"同花顺({res.get('sf_count',0)}*2.5={sf_score}) + "
            f"6+炸({res.get('bomb_6_plus',0)}*3.0={b6_score}) + "
            f"5炸({res.get('bomb_5',0)}*2.2={b5_score}) + "
            f"4炸({res.get('bomb_4',0)}*2.0={b4_score}) + "
            f"2({res.get('two_count',0)}*{two_weight_value}={two_score:.1f}) + "
            f"小王({res.get('small_joker_count',0)}*1.2={sj_score}) + "
            f"大王({res.get('big_joker_count',0)}*1.5={bj_score})"
        )

        meta["group_score"] = _group_score(res, two_weight_value)
        return meta

    best_min_turns = _with_control_meta(best_min_turns, two_weight)

    turns = best_min_turns["turns"]
    bombs = best_min_turns["bomb_count"]
    details = best_min_turns["details"]
    rh2_note = best_min_turns.get("rh2_sf_note")
    
    analysis = (
        f"- 最优方案: 预计 {turns} 轮出完, 炸弹 {bombs} 个\n"
        f"  组合: {details}"
    )
    if rh2_note:
        analysis += f"\n  注意: {rh2_note}"
    
    rec = "请ai玩家按上述最优组合方案进行出牌，优先打出组合中的散牌或小牌，保留炸弹和核心牌型。尽量不要拆牌导致完牌轮次增加。"
        
    if return_all:
        return analysis, rec, {
            "best": best_min_turns,
        }
    return analysis, rec

def get_tactical_strategies(
    game_stage: str,
    stage_focus: str,
    teammate: str,
    finished_players: list,
    is_teammate_move: bool,
    is_leader: bool,
    is_takeover: bool,
    teammate_passed_after_opponent: bool,
    last_move: dict,
    can_play_moves: list,
    has_red_heart_2: bool,
    bombs: list,
    straight_flushes: list,
    remaining_counts: dict,
    opponents: list,
    hand_structure: dict = None,
    red_heart_2_count: int = 0,
    teammate_is_leader: bool = False,
    is_self_round: bool = False,
    hand_cards: list = None,
    role: str = None,
    hand_card_ids: list = None,
    control_card_ready: dict = None
) -> tuple:
    """
    根据当前局势生成战术策略列表
    Returns: (stage_info, stage_strategies, trigger_strategies)
    """
    def get_p_name(p):
        if not p: return ""
        if isinstance(p, str): return p
        if hasattr(p, 'name'): return p.name
        return str(p)

    # 纠正首发/跟牌状态：只要桌面存在有效出牌（type!=0），就按“跟牌”处理
    if last_move and (last_move.get('type') or 0) != 0:
        is_leader = False

    # [规则限制] 过滤由红桃2（赖子）与王混合组成的非法组合
    def _is_invalid_joker_wild_combo(m):
        # [规则] 红桃2可配普通牌形成对/对/三/炸/顺等，但在任何组合中（包括炸弹）均禁止与王 (大王/小王) 同时出现。
        if (m.get('type') or 0) == 0:
            return False
            
        has_joker = False
        has_rh2 = False
        for cid in (m.get('card_ids') or []):
            cid_s = str(cid).upper()
            if any(tok in cid_s for tok in ['J20', 'J21', 'JK', 'SMALL_JOKER', 'BIG_JOKER', '小王', '大王', 'S_JK', 'B_JK']):
                has_joker = True
            if any(tok in cid_s for tok in ['H15', 'H-15', 'H2', '♥2', 'H2-']):
                has_rh2 = True
                
        return has_joker and has_rh2

    original_count = len(can_play_moves or [])
    can_play_moves = [m for m in (can_play_moves or []) if not _is_invalid_joker_wild_combo(m)]
    # if len(can_play_moves) < original_count:
    #     print(f"  [Tactics] Filtered {_is_invalid_joker_wild_combo} invalid joker+wild combos")

    rank_value_map = {
        '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
        'J': 11, 'Q': 12, 'K': 13, 'A': 14, '2': 15, '小王': 20, '大王': 21
    }
    value_to_label = {v: k for k, v in rank_value_map.items()}
    big_single_values = {15, 20, 21}

    # --- 提前提取手牌结构信息 (Pre-extract Hand Structure) ---
    singles = []
    pairs = []
    triples = []
    bombs_list = []
    singles_rank_values = set()
    pairs_rank_values = set()
    triples_rank_values = set()
    suppress_pass_strategy = False

    def normalize_rank_value(rank_value):
        if isinstance(rank_value, int):
            return rank_value
        if isinstance(rank_value, str):
            if rank_value in rank_value_map:
                return rank_value_map[rank_value]
            try:
                return int(rank_value)
            except ValueError:
                return None
        try:
            return int(rank_value)
        except (TypeError, ValueError):
            return None

    if hand_structure:
        singles = hand_structure.get('isolated_singles', [])
        pairs = hand_structure.get('pairs', [])
        triples = hand_structure.get('triples', [])
        bombs_list = hand_structure.get('bombs', [])

        for r in singles:
            rv = normalize_rank_value(r)
            if rv is not None: singles_rank_values.add(rv)
        for r in pairs:
            rv = normalize_rank_value(r)
            if rv is not None: pairs_rank_values.add(rv)
        for r in triples:
            rv = normalize_rank_value(r)
            if rv is not None: triples_rank_values.add(rv)
    # --- 提前提取活跃对手信息 (Pre-extract Opponent Stats for Suppression/Endgame) ---
    all_opp_counts = []
    max_opp_cnt = 0
    if remaining_counts and opponents:
        try:
            all_opp_counts = [
                int(remaining_counts.get(get_p_name(o), 0))
                for o in opponents
                if int(remaining_counts.get(get_p_name(o), 0)) > 0
            ]
            max_opp_cnt = max(all_opp_counts) if all_opp_counts else 0
        except Exception:
            pass

    # --- 1. 明确定义牌局对战场景 ---
    active_opponents_count = len(all_opp_counts)
    teammate_finished = (teammate in (finished_players or [])) if teammate else False
    
    # 1v1: 一对一 (牌局剩一个对手，争三游 - 队友已游)
    is_scenario_1v1 = (active_opponents_count == 1 and teammate_finished)
    # 1v2: 一对二 (牌局剩两个对手, 队友已游)
    is_scenario_1v2 = (active_opponents_count == 2 and teammate_finished)
    # 2v1: 二对一 (牌局上一个对手, 队友还在)
    is_scenario_2v1 = (active_opponents_count == 1 and not teammate_finished)
    # 2v2: 二对二 (牌局两个对手和队友都在)
    is_scenario_2v2 = (active_opponents_count == 2 and not teammate_finished)

    # 孤立战斗标志：处于 1v1 或 1v2（队友已不在场）时为 True
    is_solo_struggle = teammate_finished

    # -------------------------------------------------------------

    def is_big_single(rank_value) -> bool:
        normalized = normalize_rank_value(rank_value)
        return normalized in big_single_values if normalized is not None else False

    def is_small_combo(move_type: int, rank_value: int) -> bool:
        if move_type in (2, 3, 4, 5, 6, 7):
            return rank_value <= 12
        return False

    def is_small_single(rank_value) -> bool:
        normalized = normalize_rank_value(rank_value)
        return normalized is not None and normalized <= 12

    def label_rank(rank_str: str) -> str:
        if rank_str in rank_value_map:
            return value_to_label.get(rank_value_map[rank_str], rank_str)
        try:
            numeric = int(rank_str)
            return value_to_label.get(numeric, rank_str)
        except (TypeError, ValueError):
            return rank_str

    def format_rank_value(rank_value) -> str:
        if isinstance(rank_value, int):
            return value_to_label.get(rank_value, str(rank_value))
        if isinstance(rank_value, str):
            if rank_value in rank_value_map:
                return rank_value
            try:
                numeric = int(rank_value)
                return value_to_label.get(numeric, rank_value)
            except (TypeError, ValueError):
                return rank_value
        return str(rank_value)

    def rank_sort_key(rank_value) -> int:
        if isinstance(rank_value, int):
            return rank_value
        if isinstance(rank_value, str):
            if rank_value in rank_value_map:
                return rank_value_map[rank_value]
            try:
                return int(rank_value)
            except ValueError:
                return 99
        try:
            return int(rank_value)
        except (TypeError, ValueError):
            return 99

    def _move_splits_structure(move: dict) -> bool:
        if not move:
            return False
        rv = normalize_rank_value(move.get('rank'))
        if rv is None:
            return False

        bomb_values = set()
        for r in (bombs_list or []):
            nr = normalize_rank_value(r)
            if nr is not None:
                bomb_values.add(nr)

        if move.get('type') == 1:
            return (rv in (pairs_rank_values or set())) or (rv in (triples_rank_values or set())) or (rv in bomb_values)
        if move.get('type') == 2:
            return (rv in (triples_rank_values or set())) or (rv in bomb_values)
        if move.get('type') == 3:
            return rv in bomb_values
        return False

    def lead_bonus_for_move(move: dict) -> int:
        """Return 1 only if the follow move is a control-worthy big move.

        Rule: bombs/straight-flush or rank >= 2 (i.e., 2/Jokers) count for gaining next lead.
        """
        if not move:
            return 0
        mt = move.get('type') or 0
        if mt >= 20 or mt == 30:
            return 1
        rv = normalize_rank_value(move.get('rank'))
        return 1 if (rv is not None and rv >= 15) else 0

    def describe_pad_moves(moves: list, move_kind: str, reverse: bool = False) -> str:
        if not moves:
            return ""
        sorted_moves = sorted(
            moves,
            key=lambda m: (rank_sort_key(m.get('rank')), len(m.get('card_ids') or [])),
            reverse=reverse
        )
        preview = []
        for mv in sorted_moves[:3]:
            rank_label = format_rank_value(mv.get('rank'))
            if move_kind == 'single':
                preview.append(rank_label)
            elif move_kind == 'pair':
                preview.append(f"对{rank_label}")
            elif move_kind == 'triple':
                preview.append(f"三张{rank_label}")
            elif move_kind == 'triple_pair':
                preview.append(f"三带二({rank_label})")
            elif move_kind == 'straight':
                length = len(mv.get('card_ids') or [])
                preview.append(f"{length}张顺子({rank_label}起)")
            elif move_kind == 'consecutive_pairs':
                ranks = parse_consecutive_pairs_ranks(mv) or []
                if ranks:
                    preview.append(f"{format_rank_value(ranks[0])}-{format_rank_value(ranks[-1])}连对")
                else:
                    preview.append("连对")
            elif move_kind == 'plate':
                low, high = parse_plate_ranks(mv) or (None, None)
                if low is not None and high is not None:
                    preview.append(f"钢板{format_rank_value(low)}-{format_rank_value(high)}")
                else:
                    preview.append("钢板")
        detail = "；示例：" + ", ".join(preview)
        if len(sorted_moves) > len(preview):
            detail += f" 等{len(sorted_moves)}种"
        return detail

    def describe_bomb_moves(moves: list) -> str:
        if not moves:
            return ""
        sorted_moves = sorted(
            moves,
            key=lambda m: (-len(m.get('card_ids') or []), rank_sort_key(m.get('rank')))
        )
        preview = []
        for mv in sorted_moves[:3]:
            rank_label = format_rank_value(mv.get('rank'))
            length = len(mv.get('card_ids') or [])
            preview.append(f"{length}张炸弹({rank_label})")
        detail = "；示例：" + ", ".join(preview)
        if len(sorted_moves) > len(preview):
            detail += f" 等{len(sorted_moves)}种"
        return detail

    def describe_split_bomb_normal_moves(moves: list) -> str:
        if not moves:
            return ""

        type_name = {
            1: "单张",
            2: "对子",
            3: "三张",
            4: "三带二",
            5: "顺子",
            6: "连对",
            7: "钢板",
        }

        sorted_moves = sorted(
            moves,
            key=lambda m: (rank_sort_key(m.get('rank')), len(m.get('card_ids') or []), m.get('type') or 0)
        )
        preview = []
        for mv in sorted_moves[:5]:
            mt = mv.get('type') or 0
            rank_label = format_rank_value(mv.get('rank'))
            label = type_name.get(mt, f"普通牌型(type={mt})")
            if mt == 2:
                preview.append(f"{label}({rank_label})")
            elif mt == 4:
                preview.append(f"{label}({rank_label})")
            elif mt in (1, 3, 5, 7):
                preview.append(f"{label}({rank_label})")
            elif mt == 6:
                ranks = parse_consecutive_pairs_ranks(mv) or []
                if ranks:
                    preview.append(f"{label}({format_rank_value(ranks[0])}-{format_rank_value(ranks[-1])})")
                else:
                    preview.append(label)
            else:
                preview.append(f"{label}({rank_label})")

        detail = "；示例：" + ", ".join(preview)
        if len(sorted_moves) > len(preview):
            detail += f" 等{len(sorted_moves)}种"
        return detail

    def parse_rank_from_card_id(card_id) -> Optional[int]:
        """Parse rank value from card id like 'H15-0'/'S11-1'/'J20-0'."""
        if not card_id:
            return None
        try:
            s = str(card_id)
            if '-' in s:
                prefix = s.split('-', 1)[0]  # e.g. H15 / J20
                if len(prefix) < 2:
                    return None
                rank_part = prefix[1:]
                if rank_part.isdigit():
                    return int(rank_part)
            # 兼容花色符号/字母前缀与中文王
            rank_map = {
                '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
                'J': 11, 'Q': 12, 'K': 13, 'A': 14, '2': 15,
                '小王': 20, '大王': 21
            }
            if 'JK' in s and '大王' in s:
                return 21
            if 'JK' in s and '小王' in s:
                return 20
            if '大王' in s:
                return 21
            if '小王' in s:
                return 20
            s_clean = (
                s.replace('♣', '')
                .replace('♠', '')
                .replace('♦', '')
                .replace('♥', '')
                .replace('H', '')
                .replace('S', '')
                .replace('C', '')
                .replace('D', '')
                .replace('JK', '')
            ).strip()
            if s_clean in rank_map:
                return rank_map[s_clean]
            if s_clean.isdigit():
                return int(s_clean)
        except Exception:
            return None
        return None

    def _move_control_bonus(move: dict, two_weight_value: float) -> float:
        """本轮出2/王/炸弹的控牌分加成（用于跟牌组牌分对比）"""
        if not move:
            return 0.0
        bonus = 0.0
        mt = move.get('type') or 0
        if mt == 30:
            bonus += 2.5
        elif mt >= 20:
            blen = len(move.get('card_ids') or [])
            bonus += 2.5 if blen >= 6 else 2.0

        counts = {}
        for cid in (move.get('card_ids') or []):
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                continue
            counts[rv] = counts.get(rv, 0) + 1
        if counts.get(15, 0) > 0:
            bonus += counts[15] * float(two_weight_value)
        if counts.get(20, 0) > 0:
            bonus += counts[20] * 1.2
        if counts.get(21, 0) > 0:
            bonus += counts[21] * 1.5

        return bonus

    def move_uses_red_heart_2(move: dict) -> bool:
        try:
            for cid in (move.get('card_ids') or []):
                s = str(cid).upper()
                if s.startswith('H15') or 'H15' in s or 'H-15' in s or '♥2' in s or s.startswith('H2') or 'H2-' in s:
                    return True
        except Exception:
            return False
        return False

    def parse_plate_ranks(move: dict) -> Optional[Tuple[int, int]]:
        """If move is a plate (two consecutive triples, 6 cards), return (low_rank, high_rank)."""
        if not move:
            return None
        card_ids = move.get('card_ids') or []
        if len(card_ids) != 6:
            return None

        counts = {}
        for cid in card_ids:
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                return None
            counts[rv] = counts.get(rv, 0) + 1

        if len(counts) != 2:
            return None
        ranks = sorted(counts.keys())
        if any(counts[r] != 3 for r in ranks):
            return None
        if any(r >= 15 for r in ranks):
            return None
        if ranks[1] != ranks[0] + 1:
            return None
        return (ranks[0], ranks[1])

    def is_plate_move(move: dict) -> bool:
        return parse_plate_ranks(move) is not None

    def describe_plate_moves(moves: list) -> str:
        if not moves:
            return ""
        items = []
        for mv in moves:
            pr = parse_plate_ranks(mv)
            if not pr:
                continue
            low, high = pr
            items.append((low, mv))
        items.sort(key=lambda x: x[0])
        preview = []
        for _, mv in items[:3]:
            low, high = parse_plate_ranks(mv) or (None, None)
            if low is None:
                continue
            preview.append(f"钢板{format_rank_value(low)}-{format_rank_value(high)}")
        detail = "；示例：" + ", ".join(preview)
        if len(items) > len(preview):
            detail += f" 等{len(items)}种"
        return detail

    def remaining_rank_counts_after_move(hand_cards_list: list, move: dict) -> dict[int, int]:
        """Simulate remaining rank counts after playing `move` by removing its card_ids from hand_cards."""
        used = set(move.get('card_ids') or [])
        remain_ids = [cid for cid in (hand_cards_list or []) if cid not in used]
        counts: dict[int, int] = {}
        for cid in remain_ids:
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                continue
            counts[rv] = counts.get(rv, 0) + 1
        return counts

    def rank_follow_moves_by_structure_loss(
        hand_cards_list: list,
        candidates: list[dict],
        triples_rank_values: set[int]
    ) -> tuple[list[dict], list[dict]]:
        """Rank follow-move candidates by structure loss (new singles count).

        Returns:
            (low_loss_candidates, high_loss_candidates)
        """
        if not candidates or not hand_cards_list:
            return (candidates, [])

        scored = []
        for mv in candidates:
            remain_counts = remaining_rank_counts_after_move(hand_cards_list, mv)
            singles_after = [r for r, c in remain_counts.items() if c == 1]
            new_singles_count = len(singles_after)

            # 额外惩罚：若该出牌会拆散三张（例如 888 只用了两张），则优先级降低
            penalty = 0
            counts_in_move: dict[int, int] = {}
            for cid in (mv.get('card_ids') or []):
                rv = parse_rank_from_card_id(cid)
                if rv is None:
                    continue
                counts_in_move[rv] = counts_in_move.get(rv, 0) + 1

            for tr in (triples_rank_values or set()):
                if 0 < counts_in_move.get(tr, 0) < 3:
                    penalty += 5  # 拆三张惩罚

            scored.append((new_singles_count + penalty, mv))

        scored.sort(key=lambda x: x[0])
        min_score = scored[0][0]

        low_loss = [mv for score, mv in scored if score <= min_score + 1]
        high_loss = [mv for score, mv in scored if score > min_score + 1]

        return (low_loss, high_loss)

    def release_risk_tuple(hand_cards_list: list, move: dict) -> tuple[int, int, int, int, int, int]:
        """Lower is better: (bomb_split_penalty, total_singles, small_singles, -pairs_left, -triples_left, remaining_cards)."""
        counts = remaining_rank_counts_after_move(hand_cards_list, move)
        singles_vals = [r for r, c in counts.items() if c == 1]
        total_singles = len(singles_vals)
        small_singles = sum(1 for r in singles_vals if r <= 12)
        pairs_left = sum(1 for c in counts.values() if c == 2)
        triples_left = sum(1 for c in counts.values() if c == 3)
        remaining_cards = sum(counts.values())
        
        # 计算拆炸弹惩罚 (Penalty for splitting a natural bomb)
        bomb_split_penalty = 0
        if hand_cards_list:
            hand_counts = {}
            for cid in hand_cards_list:
                rv = parse_rank_from_card_id(cid)
                if rv is not None:
                    hand_counts[rv] = hand_counts.get(rv, 0) + 1
            
            # 记录移动中的点数消耗
            move_counts = {}
            for cid in (move.get('card_ids') or []):
                rv = parse_rank_from_card_id(cid)
                if rv is not None:
                    move_counts[rv] = move_counts.get(rv, 0) + 1
            
            for rv, have in hand_counts.items():
                if have >= 4:
                    used = move_counts.get(rv, 0)
                    if 0 < used < have: # 拆了炸弹
                        bomb_split_penalty += 1 # 每拆一个炸弹加1分惩罚
        
        return (bomb_split_penalty, total_singles, small_singles, -pairs_left, -triples_left, remaining_cards)

    def parse_consecutive_pairs_ranks(move: dict) -> Optional[List[int]]:
        """If move represents consecutive pairs (>=3 pairs), return sorted distinct ranks; else None.

        Implementation is purely based on `card_ids` so it works even if upstream doesn't provide a
        dedicated move `type` for 连对.
        """
        if not move:
            return None
        card_ids = move.get('card_ids') or []
        if len(card_ids) < 6 or (len(card_ids) % 2 != 0):
            return None

        counts = {}
        for cid in card_ids:
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                return None
            counts[rv] = counts.get(rv, 0) + 1

        if not counts or any(c != 2 for c in counts.values()):
            return None

        ranks = sorted(counts.keys())
        # 连对不含2/王（与顺子/连对常规约束一致）
        if any(r >= 15 for r in ranks):
            return None
        if any(ranks[i] + 1 != ranks[i + 1] for i in range(len(ranks) - 1)):
            return None

        return ranks

    def consecutive_pair_run_ranks(pair_values: set[int]) -> set[int]:
        """Return ranks that belong to any consecutive-pair run with length>=3 (e.g. 8-9-10)."""
        vals = sorted(v for v in (pair_values or set()) if isinstance(v, int) and v < 15)
        if len(vals) < 3:
            return set()

        runs = set()
        cur = [vals[0]]
        for v in vals[1:]:
            if v == cur[-1] + 1:
                cur.append(v)
            else:
                if len(cur) >= 3:
                    runs.update(cur)
                cur = [v]
        if len(cur) >= 3:
            runs.update(cur)
        return runs

    def split_fullhouse_ranks(move: dict) -> Tuple[Optional[int], Optional[int]]:
        """Return (triple_rank, pair_rank) for type-4 move based on card_ids."""
        if not move or move.get('type') != 4:
            return (None, None)
        card_ids = move.get('card_ids') or []
        if len(card_ids) < 5:
            return (None, None)

        counts = {}
        for cid in card_ids:
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                continue
            counts[rv] = counts.get(rv, 0) + 1

        triple_rank = None
        pair_rank = None
        for rv, c in counts.items():
            if c == 3:
                triple_rank = rv
            elif c == 2:
                pair_rank = rv
        return (triple_rank, pair_rank)

    def describe_fullhouse_moves(moves: list) -> str:
        if not moves:
            return ""
        sorted_moves = sorted(moves, key=lambda m: rank_sort_key(m.get('rank')))
        preview = []
        for mv in sorted_moves[:3]:
            t_rank, p_rank = split_fullhouse_ranks(mv)
            t_lab = format_rank_value(t_rank) if t_rank is not None else format_rank_value(mv.get('rank'))
            p_lab = format_rank_value(p_rank) if p_rank is not None else "?"
            preview.append(f"三张{t_lab}带对{p_lab}")
        detail = "；示例：" + ", ".join(preview)
        if len(sorted_moves) > len(preview):
            detail += f" 等{len(sorted_moves)}种"
        return detail

    def next_player_of(player: str) -> Optional[str]:
        rotation = ["User", "RightBot", "PartnerBot", "LeftBot"]
        try:
            idx = rotation.index(player)
        except Exception:
            return None
        return rotation[(idx + 1) % len(rotation)]

    def beats_rank(my_rank, target_rank) -> bool:
        my_value = normalize_rank_value(my_rank)
        target_value = normalize_rank_value(target_rank)
        if my_value is None or target_value is None:
            return True
        return my_value > target_value

    def filter_by_structure(moves: list, allowed_values: set) -> list:
        if not allowed_values:
            return moves
        filtered = []
        for mv in moves:
            normalized = normalize_rank_value(mv.get('rank'))
            if normalized in allowed_values:
                filtered.append(mv)
        return filtered

    def count_twos_in_hand() -> int:
        """Count rank-2 cards (rank value 15) in current hand.

        Prefers `hand_cards` when available; falls back to `hand_structure` signals.
        """
        if hand_cards:
            count = 0
            for cid in hand_cards:
                try:
                    prefix = str(cid).split('-', 1)[0]  # e.g. H15
                    if not prefix:
                        continue
                    rank_part = prefix[1:]
                    if rank_part.isdigit() and int(rank_part) == 15:
                        count += 1
                except Exception:
                    continue
            return count

        max_count = 0
        if hand_structure:
            if bombs_list and '2' in bombs_list:
                max_count = max(max_count, 4)
            if triples and '2' in triples:
                max_count = max(max_count, 3)
            if pairs and '2' in pairs:
                max_count = max(max_count, 2)
            if singles and '2' in singles:
                max_count = max(max_count, 1)
        return max_count

    def _control_card_labels_in_hand() -> list[str]:
        ready = control_card_ready or {}
        labels = []
        if singles:
            for s in singles:
                if s in ['A', '2', '小王', '大王'] and ready.get(s, False):
                    labels.append(f"单{s}")
        if pairs:
            for p in pairs:
                if p in ['A', '2', '小王', '大王'] and ready.get(p, False):
                    labels.append(f"对{p}")
        if not labels and hand_cards:
            # 兜底：手牌列表中有A/2/王
            has_a = ready.get('A', False) and any(('A' in str(c) or '14' in str(c)) for c in hand_cards)
            has_2 = ready.get('2', False) and any(('2' in str(c) or '15' in str(c)) for c in hand_cards)
            has_sj = ready.get('小王', False) and any(('小王' in str(c) or 'JK小王' in str(c)) for c in hand_cards)
            has_bj = ready.get('大王', False) and any(('大王' in str(c) or 'JK大王' in str(c)) for c in hand_cards)
            if has_a: labels.append("A")
            if has_2: labels.append("2")
            if has_sj: labels.append("小王")
            if has_bj: labels.append("大王")
        return labels

    stage_info = (f"当前牌局阶段：{game_stage}", stage_focus)
    if game_stage == "残局阶段":
        stage_strategies = list(TACTICS_DB["stages"]["end_game"])
        if remaining_counts and opponents:
            try:
                opp_has_leq3 = any(0 < int(remaining_counts.get(get_p_name(o), 0)) <= 3 for o in opponents)
            except Exception:
                opp_has_leq3 = False
            if opp_has_leq3:
                stage_strategies = [
                    s for s in stage_strategies
                    if not (isinstance(s, (tuple, list)) and len(s) >= 1 and s[0] == "残局首发有孤牌")
                ]
    elif game_stage == "中局阶段":
        stage_strategies = list(TACTICS_DB["stages"]["mid_game"])
    else:
        stage_strategies = list(TACTICS_DB["stages"]["opening"])

    trigger_strategies = []
    flow_strategies = []
    optimization_data = None
    optimization_turns = None
    avoid_wild_hint_added = False
    avoid_same_count_active = False
    mode_note = ""
    extra_detail = ""

    if hand_cards or hand_card_ids:
        # [ADD] 队友已游/接风 专项检测 (放在最前面，确保最高优)
        if (teammate in (finished_players or [])):
            if is_teammate_move:
                 # [Fix] 检查下家对手是否听牌（牌数=牌面数），若是则此时不能PASS接风，必须拦截！
                 
                 # 1. 确定我的身份 (既然已知teammate，反推myself)
                 my_role = None
                 role_pairs = {
                     "PartnerBot": "User", "User": "PartnerBot", 
                     "RightBot": "LeftBot", "LeftBot": "RightBot"
                 }
                 my_role = role_pairs.get(teammate)
                 
                 critical_block = False
                 if my_role:
                     next_p = next_player_of(my_role)
                     # 确保 next_p 是对手（且未游）
                     if next_p and next_p != teammate and next_p not in (finished_players or []):
                          try:
                              op_count = int(remaining_counts.get(get_p_name(next_p), 99))
                          except:
                              op_count = 99
                          
                          # 获取当前牌面张数
                          current_cards_len = 0
                          if last_move and last_move.get('card_ids'):
                              current_cards_len = len(last_move['card_ids'])
                          
                          # 如果对手牌数 == 牌面张数 -> 危险！必须拦！
                          if current_cards_len > 0 and op_count == current_cards_len:
                              # [Fix] 补全策略：如果队友出的牌足够大（2/王/炸弹等），则无需恐慌拦截，可接风
                              is_tm_secure = False
                              if last_move:
                                  tm_rank = normalize_rank_value(last_move.get('rank'))
                                  tm_type = last_move.get('type', 0)
                                  # 判定安全：炸弹(>=20) 或 点数>=15(2,小王,大王)
                                  if tm_type >= 20 or (tm_rank is not None and tm_rank >= 15):
                                      is_tm_secure = True
                              
                              # 另外：如果我能直接斩杀（手牌数==出牌数 && 能管上），会有后续 jiefeng_strat_list 提示 "能完牌->直接管"，
                              # 所以这里 critical_block 主要负责 "我不能斩杀，但必须防守" 的情况。
                              # 如果队友牌安全，就不触发强制拦截，走正常的接风/顺牌逻辑。
                              if not is_tm_secure:
                                  critical_block = True
                                  trigger_strategies.insert(0, (
                                      "【警报】下家听牌拦截", 
                                      f"下家对手({next_p})仅剩 {op_count} 张牌，且队友牌力不足以绝对压制！**必须立刻拦截**！不可PASS！"
                                  ))

                 if not critical_block and not is_leader:
                     jiefeng_strat_list = TACTICS_DB["teammate"].get("jiefeng_wait_hint", [])
                     if jiefeng_strat_list:
                         trigger_strategies.insert(0, jiefeng_strat_list[0])

        # [ADD] 队友打大牌，自己剩炸弹时的让路提示
        elif is_teammate_move and last_move:
             tm_rank_val = normalize_rank_value(last_move.get('rank'))
             tm_type_val = last_move.get('type', 0)
             is_tm_big = (tm_rank_val is not None and tm_rank_val >= 14) or (tm_type_val >= 20)
             
             # 若队友牌大，且我只有炸弹(<=6张保守估计)，且对手安全
             if is_tm_big and bombs and len(hand_cards or []) <= 6:
                  all_safe = True
                  if opponents and remaining_counts:
                      for op in opponents:
                           if remaining_counts.get(get_p_name(op), 0) <= 6:
                                all_safe = False; break
                  if all_safe:
                       strat_list = TACTICS_DB["teammate"].get("bomb_defer_win", [])
                       if strat_list:
                           trigger_strategies.append(strat_list[0])

        optimization_hand = hand_card_ids or hand_cards

        def _two_weight_by_stage(stage: str) -> float:
            if stage == "开局阶段":
                return 0.6
            if stage == "中局阶段":
                return 0.8
            if stage == "残局阶段":
                return 1.0
            return 1.0

        two_weight = _two_weight_by_stage(game_stage)

        analysis_text, recommendation_text, strategy_bundle = calculate_hand_optimization(
            optimization_hand,
            return_all=True,
            two_weight=two_weight
        )
        
        # 预先计算关键残局标志位，供策略建议使用
        is_endgame_critical_finish = False
        opp_in_danger_zone = False
        best_plan_global = strategy_bundle.get("best", {}) if strategy_bundle else {}
        if best_plan_global:
            try:
                optimization_turns = int(best_plan_global.get("turns", 0))
                # 斩杀判定：仅剩2轮或1轮（带炸弹且轮转1轮）
                if optimization_turns <= 2:
                    is_endgame_critical_finish = True
            except Exception:
                optimization_turns = None
        
        if remaining_counts and opponents:
            try:
                opp_in_danger_zone = any(0 < int(remaining_counts.get(get_p_name(o), 99)) <= 3 for o in (opponents or []))
            except Exception:
                pass

        if strategy_bundle and optimization_turns is None:
            # ... (redundant but kept for structure)
            pass

        def _format_control_meta(meta: dict) -> str:
            return (
                f"同花顺{meta.get('sf_count', 0)}"
                f"/4-5炸弹{meta.get('bomb_4_5', 0)}"
                f"/6+炸弹{meta.get('bomb_6_plus', 0)}"
                f"/二{meta.get('two_count', 0)}"
                f"/小王{meta.get('small_joker_count', 0)}"
                f"/大王{meta.get('big_joker_count', 0)}"
            )

        if is_leader:
            best_plan = strategy_bundle.get("best", {})

            optimization_turns = int(best_plan.get("turns", 0)) if best_plan else None

            def _format_strategy_line(label: str, meta: dict) -> str:
                breakdown = meta.get('control_score_breakdown', '')
                breakdown_str = f"\n  组牌分详情: {breakdown}" if breakdown else ""
                
                rh2_note = meta.get('rh2_sf_note')
                rh2_note_str = f"\n  万能牌决策: {rh2_note}" if rh2_note else ""
                
                return (
                    f"{label}"
                    f"预计 {meta.get('turns', '?')} 轮出完、控制分 {meta.get('control_score', '?')}、小孤张 {meta.get('small_singles', '?')} 个"
                    f"{breakdown_str}"
                    f"{rh2_note_str}\n"
                    f"  组合: {meta.get('details', '')}"
                )

            strategy_lines = []
            if best_plan:
                strategy_lines.append(_format_strategy_line("【唯一最优策略】", best_plan))

            # 动态调整建议策略
            rec_text = "请ai玩家根据当前牌局情况从上述的最优组合牌型中选择一个牌型来出牌，开局阶段优先清理散牌或小牌，中局阶段可利用控牌机会打更有利于最优组牌策略的选项，尽量不要拆牌导致完牌轮次增加。"
            if is_endgame_critical_finish or opp_in_danger_zone:
                rec_text = "【残局严控】当前处于胜负关键时刻（你已进入斩杀线或对手极其危险）。**必须优先参考“牌局触发提醒”中的拦截与防守原则**，而不是机械执行上述组牌方案。在确保能控权的前提下，再按照该优化组合进行最终清场。"

            optimization_data = {
                "title": "组牌最优策略（系统推演）",
                "content": (
                    "\n".join(strategy_lines)
                    + "\n"
                    + f"**建议策略**：{rec_text}"
                ),
                "scope_label": "",
            }
            
            # 若有特殊的万能牌决策，额外增加一个醒目的提醒
            if best_plan and best_plan.get("rh2_sf_note"):
                trigger_strategies.insert(0, ("【万能牌决策提醒】", best_plan["rh2_sf_note"]))
        else:
            # 跟牌：仅评估当前牌型可跟的候选 + PASS
            follow_candidates = []
            pass_candidate = {"desc": "PASS", "card_ids": []}

            if last_move and can_play_moves:
                target_type = last_move.get('type')
                target_len = len(last_move.get('card_ids') or [])
                follow_candidates = [
                    m for m in (can_play_moves or [])
                    if m.get('type') == target_type and len(m.get('card_ids') or []) == target_len
                ]

                # 追加炸弹/同花顺候选（可压任意普通牌型）
                bomb_candidates = [
                    m for m in (can_play_moves or [])
                    if (m.get('type') or 0) >= 20 or (m.get('type') == 30)
                ]
                follow_candidates.extend(bomb_candidates)

                # 去重（按 id）
                seen = set()
                deduped = []
                for mv in follow_candidates:
                    mid = mv.get('id')
                    if mid in seen:
                        continue
                    seen.add(mid)
                    deduped.append(mv)
                follow_candidates = deduped

            # 排序并限制数量，避免提示过长
            follow_candidates = sorted(
                follow_candidates,
                key=lambda m: (rank_sort_key(m.get('rank')), len(m.get('card_ids') or []))
            )

            # 单张/对子/三张跟牌：不裁剪同型候选，避免漏掉更优方案
            if target_type in (1, 2, 3):
                # 保留所有同型跟牌，炸弹已在上面追加
                pass
            else:
                follow_candidates = follow_candidates[:6]

            eval_items = []

            def _fmt_line(desc: str, best: dict) -> str:
                return (
                    f"{desc} → "
                    f"完牌轮次:{best.get('turns', '?')}轮/小孤张{best.get('small_singles', '?')}；"
                    f"控牌能力:{best.get('control_score', '?')}（{_format_control_meta(best)}）；"
                    f"组牌分:{best.get('group_score', '?')}"
                )

            # PASS 方案
            try:
                _, _, bundle_pass = calculate_hand_optimization(
                    optimization_hand,
                    return_all=True,
                    two_weight=two_weight
                )
                eval_items.append({
                    "desc": "PASS",
                    "line": _fmt_line(
                        "PASS",
                        bundle_pass.get("best", {})
                    ),
                    "best": bundle_pass.get("best", {})
                })
            except Exception:
                pass

            # 跟牌候选方案
            for mv in follow_candidates:
                used = set(mv.get('card_ids') or [])
                remaining = [cid for cid in (optimization_hand or []) if cid not in used]
                if not remaining:
                    remaining = []
                _, _, bundle_follow = calculate_hand_optimization(
                    remaining,
                    return_all=True,
                    two_weight=two_weight
                )
                desc = mv.get('desc') or f"type={mv.get('type')} rank={format_rank_value(mv.get('rank'))}"
                best_follow = dict(bundle_follow.get("best", {}) or {})
                move_bonus = _move_control_bonus(mv, two_weight)
                if best_follow:
                    best_follow["group_score"] = float(best_follow.get("group_score", 0) or 0) - (move_bonus / 2.0)
                eval_items.append({
                    "desc": desc,
                    "line": _fmt_line(
                        desc,
                        best_follow
                    ),
                    "best": best_follow
                })

            def _group_val(meta: dict) -> float:
                return float(meta.get("group_score", 9999) or 9999)

            eval_lines = []
            recommendation_lines = []
            if eval_items:
                pass_item = next((item for item in eval_items if item.get("desc") == "PASS"), None)
                follow_items = [item for item in eval_items if item.get("desc") != "PASS"]

                best_item = None
                if pass_item and follow_items:
                    best_follow = min(follow_items, key=lambda x: _group_val(x.get("best", {})))
                    if _group_val(best_follow.get("best", {})) <= _group_val(pass_item.get("best", {})):
                        best_item = best_follow

                if not best_item:
                    # 默认仍按最低组牌分，但在同分时按以下权重优选：
                    # 1. A以下的孤张更少 (fewer small singles)
                    # 2. 优先选择出牌 (prefer NOT PASS if group score/singles are tied)
                    best_item = min(
                        eval_items,
                        key=lambda x: (
                            _group_val(x.get("best", {})),
                            x.get("best", {}).get("small_singles", 99),
                            0 if x.get("desc") != "PASS" else 1
                        )
                    )

                eval_lines = [best_item["line"]]
                details = (best_item.get("best") or {}).get("details")
                if details:
                    recommendation_lines = [f"{best_item.get('desc', '')}：{details}"]

            if eval_lines:
                content = "\n".join(eval_lines)
                if recommendation_lines:
                    content += "\n具体跟牌组牌方案:\n" + "\n".join(recommendation_lines)
            else:
                content = "暂无可评估的跟牌候选"

            optimization_data = {
                "title": "【高优先】（系统推演）",
                "content": content,
                "scope_label": "跟牌后组牌最优策略",
            }

    should_prompt_flow = (game_stage in ["开局阶段", "中局阶段"]) and (not is_leader) and bool(last_move)
    if should_prompt_flow:
        # 如果是队友出牌，且是小牌，则不强制提示“过牌策略”，允许顺牌评估
        if is_teammate_move:
            lm_rank = normalize_rank_value(last_move.get('rank'))
            if lm_rank is not None and lm_rank <= 12:
                 should_prompt_flow = False

    if should_prompt_flow:
        flow_strategies.append(TACTICS_DB["situational"]["pass_strategy"])

    # 2.5 可控牌大牌价值最大化 (Control Value Maximization)
    if hand_structure:
        # 新策略触发：保守炸弹逻辑（当有炸弹且需要拦截普通牌型时）
        if not is_leader and last_move and (last_move.get('type', 0) < 20) and (bombs_list or bombs):
             trigger_strategies.append(TACTICS_DB["situational"]["conservative_bomb_logic"])

        if any(r in ['2', '小王', '大王'] for r in singles):
            if game_stage != "残局阶段":
                trigger_strategies.append(TACTICS_DB["control_value"]["single_2_joker"])

        has_big_pair = any(r in ['2', '小王', '大王'] for r in pairs)
        has_big_triple = any(r in ['2', '小王', '大王'] for r in triples)
        if has_big_pair or has_big_triple:
            if game_stage != "残局阶段":
                trigger_strategies.append(TACTICS_DB["control_value"]["pair_triple_2_joker"])

        # 检测"四张2"（包括红桃2凑成的）
        has_four_2 = False
        if hand_cards:
            # 统计非红桃2的2的张数
            count_2 = sum(1 for cid in hand_cards 
                         if isinstance(cid, str) 
                         and not (cid.startswith('H15') or '♥2' in cid or cid == 'H2')
                         and ('-' in cid and cid.split('-')[0].endswith('15')))
            # 如果有3张2+红桃2，或自然4张2，都算有四张2
            has_four_2 = (count_2 >= 4) or (count_2 == 3 and has_red_heart_2)
        
        if has_four_2:
            trigger_strategies.append(TACTICS_DB["control_value"]["four_2"])

        # 提醒：仅当推演为4轮出尽且存在三张+单牌时，强调不存在三带一
        if optimization_turns == 4 and triples and singles:
            trigger_strategies.append(TACTICS_DB["situational"]["no_triple_single_combo"])

        # 提醒：两张红桃2尽量分开用于不同结构
        if red_heart_2_count >= 2:
            trigger_strategies.append(TACTICS_DB["situational"]["split_two_wilds_across_structures"])

        # 1v1 / 1v2 斩杀提醒（抑制其他触发提醒）
        if remaining_counts and optimization_turns == 2:
            try:
                active_opponents = [
                    o for o in (opponents or [])
                    if (get_p_name(o) not in (finished_players or [])) and int(remaining_counts.get(get_p_name(o), 0)) > 0
                ]
            except Exception:
                active_opponents = []

            is_1v1 = (len(active_opponents) == 1)
            is_1v2 = (len(active_opponents) == 2)
            opp_counts = [int(remaining_counts.get(get_p_name(o), 99)) for o in active_opponents]

            cond_1v1 = is_1v1 and opp_counts and opp_counts[0] <= 5
            cond_1v2 = is_1v2 and opp_counts and all(c <= 3 for c in opp_counts)

            if cond_1v1 or cond_1v2:
                control_labels = _control_card_labels_in_hand()
                if control_labels:
                    readable = "、".join(control_labels)
                    trigger_strategies = [(
                        "【最高优先】控牌斩杀提醒",
                        f"当前{('1v1' if cond_1v1 else '1v2')}，对手剩牌接近斩杀线，你仅需两轮出尽。"
                        f"你手中有可控牌（{readable}）为单牌/对子中已是最大，可用于控牌斩杀。"
                        "请优先用控牌争取下一轮首发或逼出对手炸弹，完成斩杀。"
                    )]
                    flow_strategies = []
                    suppress_pass_strategy = True

        # 检测"6张及以上大炸弹"或"可用红桃2凑成6+炸弹"
        has_big_bomb_or_upgradable = False
        if hand_cards:
            from collections import Counter
            
            # 统计手牌中每个点数的张数
            hand_rank_counts = Counter()
            for cid in hand_cards:
                # 解析点数（跳过红桃2）
                if isinstance(cid, str):
                    if cid.startswith('H15') or '♥2' in cid or cid == 'H2':
                        continue  # 红桃2单独处理
                    
                    # 尝试解析点数值
                    try:
                        if '-' in cid:
                            parts = cid.split('-')
                            if len(parts[0]) > 1:
                                rank_str = parts[0][1:]
                                rank_val = int(rank_str)
                                hand_rank_counts[rank_val] += 1
                    except Exception:
                        pass
            
            # 检查是否有6+炸弹
            has_6_bomb = any(c >= 6 for c in hand_rank_counts.values())
            
            # 检查是否有5张+红桃2可凑成6炸
            has_5_upgradable = (
                has_red_heart_2 
                and any(c == 5 for c in hand_rank_counts.values())
            )
            
            has_big_bomb_or_upgradable = has_6_bomb or has_5_upgradable
        
        # 开局阶段也触发6张炸弹相关策略提示，但按局势选择不冲突的版本：
        # - 若对手已头游/胜势在握：提示“降倍/可拆”
        # - 若本方头游已确认（队友已完牌头游 / 自己一手出尽可稳获头游）：提示“争胜/冲刺头游时优先翻倍”
        # - 否则（头游未确认）：提示“红桃2升6+炸仅限确认头游时”，防止白送翻倍
        if has_big_bomb_or_upgradable:
            opponents_finished = False
            try:
                opponents_finished = any((p in (finished_players or [])) for p in (opponents or []))
            except Exception:
                opponents_finished = False

            head_win_confirmed = False
            try:
                head_win_confirmed = teammate_finished or (optimization_turns == 1)
            except Exception:
                head_win_confirmed = False

            if opponents_finished:
                trigger_strategies.append(TACTICS_DB["control_value"]["big_bomb_split_when_opp_head"])
            elif head_win_confirmed:
                trigger_strategies.append(TACTICS_DB["control_value"]["big_bomb_use_for_multiplier"])
            else:
                trigger_strategies.append(TACTICS_DB["control_value"]["rh2_6bomb_only_when_head_win_confirmed"])

    pair_run_ranks = consecutive_pair_run_ranks(pairs_rank_values)

    # 三带二：避免用对A/对2/对王作对子（除非必须拦截或可直接斩杀）
    big_pair_values = {14, 15, 20, 21}  # A / 2 / 小王 / 大王
    fullhouse_moves = [m for m in (can_play_moves or []) if m.get('type') == 4]
    should_check_fullhouse = bool(is_leader) or (bool(last_move) and last_move.get('type') == 4)
    if should_check_fullhouse and fullhouse_moves:
        big_pair_fullhouses = []
        normal_pair_fullhouses = []
        for mv in fullhouse_moves:
            _, pair_rank = split_fullhouse_ranks(mv)
            if pair_rank in big_pair_values:
                big_pair_fullhouses.append(mv)
            else:
                normal_pair_fullhouses.append(mv)

        if big_pair_fullhouses:
            # 例外1：该三带二能直接出完（立刻获胜）
            can_kill_now = False
            if hand_cards:
                try:
                    hand_len = len(hand_cards)
                    can_kill_now = any(len(mv.get('card_ids') or []) == hand_len for mv in big_pair_fullhouses)
                except Exception:
                    can_kill_now = False

            # 例外2：必须用该类三带二拦截，否则对手将很快完牌
            forced_to_block = False
            if (
                (not is_leader)
                and last_move
                and (last_move.get('type') == 4)
                and (last_move.get('player') in (opponents or []))
                and remaining_counts
            ):
                opp = last_move.get('player')
                opp_left = remaining_counts.get(get_p_name(opp), 99)
                opponent_about_to_finish = (opp_left <= 3)

                bomb_like_moves = [m for m in (can_play_moves or []) if (m.get('type') or 0) >= 20]

                lm_rank = last_move.get('rank')
                lm_len = len(last_move.get('card_ids') or [])
                normal_fullhouse_can_beat = [
                    m for m in normal_pair_fullhouses
                    if len(m.get('card_ids') or []) == lm_len and beats_rank(m.get('rank'), lm_rank)
                ]
                big_fullhouse_can_beat = [
                    m for m in big_pair_fullhouses
                    if len(m.get('card_ids') or []) == lm_len and beats_rank(m.get('rank'), lm_rank)
                ]

                forced_to_block = bool(
                    opponent_about_to_finish
                    and big_fullhouse_can_beat
                    and (not bomb_like_moves)
                    and (not normal_fullhouse_can_beat)
                )

            if forced_to_block or can_kill_now:
                trigger_strategies.append(TACTICS_DB["situational"]["allow_big_pair_in_fullhouse_when_forced_or_kill"])
            else:
                title, desc = TACTICS_DB["situational"]["avoid_big_pair_in_fullhouse"]
                if normal_pair_fullhouses:
                    desc = desc + " 优先替代选择：" + describe_fullhouse_moves(normal_pair_fullhouses)
                else:
                    desc = desc + " 当前三带二可选均在消耗对A/对2/对王：" + describe_fullhouse_moves(big_pair_fullhouses)
                trigger_strategies.append((title, desc))

    # 2.2 应用角色与情境战术
    if teammate in finished_players and is_teammate_move and not is_leader:
        # [Audit] 仅在未触发具体的“接风/拦截”提示时，才追加通用的“队友已游”提示，避免提示冗余
        has_jiefeng_hint = any("接风" in str(s[0]) or "听牌拦截" in str(s[0]) for s in trigger_strategies)
        if not has_jiefeng_hint:
            trigger_strategies.extend(TACTICS_DB["teammate"]["jiefeng_wait_hint"])
    else:
        if is_teammate_move:
            # 检查是否为接管场景：队友出单张且对手剩1张
            should_evaluate_takeover = False
            if last_move and last_move.get('type') == 1:  # 队友出单张
                # 检查对手是否剩1张
                try:
                    opponent_has_1 = any(int(remaining_counts.get(get_p_name(opp), 99)) == 1 for opp in (opponents or []))
                except Exception:
                    opponent_has_1 = False
                
                if opponent_has_1:
                    should_evaluate_takeover = True
            
            # 根据情况应用不同规则
            if should_evaluate_takeover:
                # 应用接管评估规则（简化版）
                trigger_strategies.extend(TACTICS_DB["teammate"]["takeover_evaluation"])
            else:
                # [Mod] 区分队友出牌大小：队友出小牌(Q及以下)时允许顺牌清理废牌
                is_small_move = False
                if last_move:
                    lm_type = last_move.get('type') or 0
                    lm_rank_val = normalize_rank_value(last_move.get('rank'))
                    # 定义小牌：单/对/三/三带二/顺子/连对 根据点数判断 (<=12)
                    if lm_type in (1, 2, 3, 5, 6, 7) and lm_rank_val is not None and lm_rank_val <= 12:
                        is_small_move = True
                
                if is_small_move:
                    # 2v1专项：按对手剩牌分流
                    opp_cnt_2v1 = None
                    if is_scenario_2v1 and remaining_counts and opponents:
                        try:
                            active_opp = [
                                opp for opp in (opponents or [])
                                if get_p_name(opp) not in (finished_players or [])
                                and int(remaining_counts.get(get_p_name(opp), 0)) > 0
                            ]
                            if len(active_opp) == 1:
                                opp_cnt_2v1 = int(remaining_counts.get(get_p_name(active_opp[0]), 99))
                        except Exception:
                            opp_cnt_2v1 = None

                    if is_scenario_2v1 and opp_cnt_2v1 is not None and opp_cnt_2v1 <= 3:
                        trigger_strategies.insert(0, TACTICS_DB["situational"]["teammate_small_2v1_block_big_when_opp_low"])
                        suppress_pass_strategy = True
                    else:
                        if is_scenario_2v1 and opp_cnt_2v1 is not None and opp_cnt_2v1 > 3:
                            trigger_strategies.insert(0, TACTICS_DB["situational"]["teammate_small_2v1_follow_or_block"])
                        trigger_strategies.extend(TACTICS_DB["teammate"]["follow_small"])
                        # 既然允许顺牌，就不该强制要求PASS（除非没牌或只能拆大牌）
                        suppress_pass_strategy = True
                else:
                    # 队友出大牌（>12 或 炸弹等） -> 应用团队优先（Must Pass/优先PASS）
                    trigger_strategies.extend(TACTICS_DB["teammate"]["priority"])
                    # 绝大多数情况下队友大牌都该PASS
                    suppress_pass_strategy = False

    # === 通用残局策略：对手≤3张时评估慢打可行性 ===
    # 触发条件：首发 + 对手≤3张 + 有大牌可控 + 有小牌可完
    # [Fix] 冲突抑制：若已触发“三轮出尽-Second Best”策略（optimization_turns == 3 且有明确推荐），则不触发此慢打策略，
    # 避免AI在“出第二大”(中牌)和“慢打小牌”(小牌)之间困惑。
    is_3_turn_special = (optimization_turns == 3) # 上述 endgame_last3_leader_second_best 逻辑覆盖了 3 轮的情况

    if remaining_counts and is_leader and (not is_3_turn_special):
        try:
            any_opp_low = any(int(remaining_counts.get(get_p_name(o), 99)) <= 3 for o in (opponents or []))
        except Exception:
            any_opp_low = False

        if any_opp_low and (optimization_turns != 2):
            # 检测"自己有大牌可控"：三张2 / 炸弹 / 对王 / 对A
            has_control_resource = False
            if triples and '2' in triples:
                has_control_resource = True
            if bombs_list:
                has_control_resource = True
            if pairs:
                big_pairs = [p for p in pairs if p in ['2', 'A', '小王', '大王']]
                if big_pairs:
                    has_control_resource = True

            # 检测"自己有小牌可完"：孤张≤Q / 小对子≤10
            has_small_finish = False
            if singles:
                small_singles = [s for s in singles if normalize_rank_value(s) and normalize_rank_value(s) <= 12]
                if small_singles:
                    has_small_finish = True
            if pairs:
                small_pairs = [p for p in pairs if normalize_rank_value(p) and normalize_rank_value(p) <= 10]
                if small_pairs:
                    has_small_finish = True

            # 若同时满足"有大牌可控"+"有小牌可完"，提示慢打
            if has_control_resource and has_small_finish:
                trigger_strategies.append(TACTICS_DB["situational"]["endgame_slow_play_with_control"])

                # 追加可选的"慢打小牌"示例
                candidates = []
                if singles:
                    small_singles_moves = [
                        m for m in (can_play_moves or [])
                        if m.get('type') == 1 and normalize_rank_value(m.get('rank')) and normalize_rank_value(m.get('rank')) <= 10
                    ]
                    candidates.extend(small_singles_moves)
                if pairs:
                    small_pairs_moves = [
                        m for m in (can_play_moves or [])
                        if m.get('type') == 2 and normalize_rank_value(m.get('rank')) and normalize_rank_value(m.get('rank')) <= 10
                    ]
                    candidates.extend(small_pairs_moves)

                if candidates:
                    candidates = sorted(candidates, key=lambda m: (m.get('type'), rank_sort_key(m.get('rank'))))
                    preview = []
                    for mv in candidates[:3]:
                        rlab = format_rank_value(mv.get('rank'))
                        if mv.get('type') == 1:
                            preview.append(f"单{rlab}")
                        else:
                            preview.append(f"对{rlab}")
                    trigger_strategies.append((
                        "慢打候选（小牌试探）",
                        f"可优先考虑：{', '.join(preview)}" + (f"（共{len(candidates)}种）" if len(candidates) > len(preview) else "")
                    ))

    # 定义 build_pad_entry 辅助函数（在所有分支之前定义，确保全局可用）
    def build_pad_entry(key, moves=None, move_kind=None, reverse=False):
        title, desc = TACTICS_DB["situational"][key]
        detail = ""
        if moves and move_kind:
            detail = describe_pad_moves(moves, move_kind, reverse=reverse)
        if detail:
            desc = f"{desc} {detail}"
        return (title, desc)

    def is_split_natural_bomb_move(hand_cards_list: list, mv: dict) -> bool:
        """Return True if mv uses part of a natural bomb rank (>=4 same rank) without using all of them."""
        if not hand_cards_list or not mv:
            return False

        hand_rank_counts: dict[int, int] = {}
        for cid in (hand_cards_list or []):
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                continue
            hand_rank_counts[rv] = hand_rank_counts.get(rv, 0) + 1

        natural_bomb_ranks = {r for r, c in hand_rank_counts.items() if c >= 4}
        if not natural_bomb_ranks:
            return False

        counts_in_move: dict[int, int] = {}
        for cid in (mv.get('card_ids') or []):
            rv = parse_rank_from_card_id(cid)
            if rv is None:
                continue
            counts_in_move[rv] = counts_in_move.get(rv, 0) + 1

        for br in natural_bomb_ranks:
            used = counts_in_move.get(br, 0)
            have = hand_rank_counts.get(br, 0)
            if 0 < used < have:
                return True
        return False

    if is_leader:
        # 对手剩5张：避免首发5张普通牌型 + 首发禁止拆炸弹拼普通牌型
        if remaining_counts and opponents:
            try:
                opp_has_5 = any(int(remaining_counts.get(o, 99)) == 5 for o in (opponents or []))
            except Exception:
                opp_has_5 = False

            if opp_has_5:
                # 高优先级插入，确保覆盖普通“保留实力/控牌”等泛规则
                trigger_strategies.insert(0, TACTICS_DB["situational"]["avoid_lead_same_count_as_opp_5"])
                trigger_strategies.insert(1, TACTICS_DB["situational"]["leader_no_split_bomb_to_normal"])

                # 提供安全首发候选：单/对/三，且不拆炸弹
                if can_play_moves and hand_cards:
                    safe_types = {1, 2, 3}
                    safe_candidates = [
                        mv for mv in (can_play_moves or [])
                        if (mv.get('type') in safe_types) and (not is_split_natural_bomb_move(hand_cards, mv))
                    ]
                    if safe_candidates:
                        safe_candidates = sorted(safe_candidates, key=lambda m: (m.get('type'), rank_sort_key(m.get('rank'))))
                        preview = []
                        for mv in safe_candidates[:6]:
                            rlab = format_rank_value(mv.get('rank'))
                            if mv.get('type') == 1:
                                preview.append(f"单{rlab}")
                            elif mv.get('type') == 2:
                                preview.append(f"对{rlab}")
                            else:
                                preview.append(f"三张{rlab}")
                        trigger_strategies.insert(2, (
                            "首发安全候选（不送5张窗口 / 不拆炸弹）",
                            f"可优先考虑：{', '.join(preview)}" + (f"（共{len(safe_candidates)}种）" if len(safe_candidates) > len(preview) else "")
                        ))

                    # 明确指出风险候选：拆炸弹拼普通牌型（例如拆5炸做三带二）
                    split_bomb_normals = [
                        mv for mv in (can_play_moves or [])
                        if (mv.get('type', 0) < 20)
                        and (mv.get('type', 0) != 0)
                        and is_split_natural_bomb_move(hand_cards, mv)
                    ]
                    if split_bomb_normals:
                        trigger_strategies.insert(3, (
                            "风险提示：以下普通牌型会拆炸弹（强烈不推荐首发）",
                            "检测到若干合法选项属于‘拆炸弹拼普通牌型’：" + describe_split_bomb_normal_moves(split_bomb_normals)
                        ))

                    # 进一步提醒：对手剩5张时，尽量避免首发5张普通牌型（尤其三带二/顺子）
                    five_card_normals = [
                        mv for mv in (can_play_moves or [])
                        if (mv.get('type') in (4, 5)) and (len(mv.get('card_ids') or []) == 5)
                    ]
                    if five_card_normals:
                        five_card_normals = sorted(five_card_normals, key=lambda m: (m.get('type'), rank_sort_key(m.get('rank'))))
                        preview = []
                        for mv in five_card_normals[:4]:
                            if mv.get('type') == 4:
                                preview.append(f"三带二({format_rank_value(mv.get('rank'))})")
                            else:
                                preview.append(f"顺子(到{format_rank_value(mv.get('rank'))})")
                        trigger_strategies.insert(4, (
                            "对手剩5张：避免首发5张普通牌型",
                            "当前你有可首发的5张普通牌型（不建议先出）：" + ", ".join(preview)
                        ))

        # 放行队友：队友≤3张 + 下家手牌数与队友不同 -> 首发尽量出同张数的小牌型
        if remaining_counts and teammate and teammate not in finished_players and role:
            try:
                teammate_left = int(remaining_counts.get(teammate, 99))
            except Exception:
                teammate_left = 99

            down_player = next_player_of(role)
            try:
                down_left = int(remaining_counts.get(down_player, 99)) if down_player else 99
            except Exception:
                down_left = 99

            if teammate_left <= 3:
                remaining_opponents = [o for o in (opponents or []) if o not in (finished_players or [])]
                is_two_v_one = len(remaining_opponents) == 1
                remaining_opponent = remaining_opponents[0] if remaining_opponents else None
                
                # 特殊风险判定：下家（即你的下一顺位对手）剩牌数正好等于队友
                down_player_danger = (down_left == teammate_left)

                can_self_kill = bool(optimization_turns == 1)

                def _has_big_control() -> bool:
                    values = set()
                    for rv in (singles_rank_values or set()):
                        nv = normalize_rank_value(rv)
                        if nv is not None:
                            values.add(nv)
                    for rv in (pairs_rank_values or set()):
                        nv = normalize_rank_value(rv)
                        if nv is not None:
                            values.add(nv)
                    for rv in (triples_rank_values or set()):
                        nv = normalize_rank_value(rv)
                        if nv is not None:
                            values.add(nv)
                    if bombs_list:
                        for rv in bombs_list:
                            nv = normalize_rank_value(rv)
                            if nv is not None:
                                values.add(nv)
                    return any(v >= 15 for v in values)

                has_big_control = _has_big_control()

                # 2v1：若可斩杀，优先自己完牌，不考虑拆牌放队友
                if is_two_v_one and can_self_kill:
                    trigger_strategies.insert(0, TACTICS_DB["situational"]["priority_self_finish_2v1"])
                else:
                    # 2v1且缺少大控牌资源：允许拆牌放行队友
                    if is_two_v_one and not has_big_control:
                        trigger_strategies.insert(0, TACTICS_DB["situational"]["allow_split_lead_teammate_2v1_no_control"])
                    
                    if down_player_danger:
                        trigger_strategies.insert(0, (
                            "【关键】张数避同：避开下家报张数",
                            f"警报：下家 {down_player} 剩余张数({down_left}张)与队友一致。**严禁首发{down_left}张牌型**，否则会先放走对手。请改打其他张数（如单张或对子）来间接助攻。"
                        ))
                    
                    trigger_strategies.append(TACTICS_DB["situational"]["let_teammate_finish_when_safe"])

                # 追加可选的小牌型示例（若有合法选项）
                # 若下家张数等于队友，目标张数改为 1 (如果是2/3) 或 2 (如果是1)
                if down_player_danger:
                    actual_target = {1: 2, 2: 1, 3: 1}.get(teammate_left, 1)
                else:
                    actual_target = teammate_left

                desired_type = {1: 1, 2: 2, 3: 3}.get(actual_target)
                if desired_type and can_play_moves:
                    candidates = [m for m in can_play_moves if m.get('type') == desired_type]
                    
                    # === 新增：过滤掉"拆炸弹"的候选 ===
                    # 检测手牌中的自然炸弹点数（>=4张同点数）
                    if hand_cards:
                        hand_rank_counts = {}
                        for cid in (hand_cards or []):
                            try:
                                prefix = str(cid).split('-', 1)[0]
                                if len(prefix) > 1:
                                    rank_part = prefix[1:]
                                    if rank_part.isdigit():
                                        rv = int(rank_part)
                                        hand_rank_counts[rv] = hand_rank_counts.get(rv, 0) + 1
                            except Exception:
                                continue
                        
                        natural_bomb_ranks = {r for r, c in hand_rank_counts.items() if c >= 4}
                        
                        if natural_bomb_ranks:
                            # 过滤掉"拆炸弹"的候选
                            non_split_candidates = []
                            forbidden_ids = []
                            for mv in candidates:
                                counts_in_move = {}
                                for cid in (mv.get('card_ids') or []):
                                    rv = parse_rank_from_card_id(cid)
                                    if rv is None:
                                        continue
                                    counts_in_move[rv] = counts_in_move.get(rv, 0) + 1
                                
                                is_split = False
                                for br in natural_bomb_ranks:
                                    used = counts_in_move.get(br, 0)
                                    have = hand_rank_counts.get(br, 0)
                                    if 0 < used < have:
                                        is_split = True
                                        break
                                
                                if not is_split:
                                    non_split_candidates.append(mv)
                                else:
                                    forbidden_ids.append(str(mv.get('id')))
                            
                            # 如果有非拆炸弹的候选，优先推荐
                            if non_split_candidates:
                                candidates = non_split_candidates
                                if forbidden_ids:
                                    trigger_strategies.insert(0, (
                                        "【强制禁令】助攻严禁拆炸弹",
                                        f"当前有碎牌可供助攻。你**绝对禁止**选择 ID [{', '.join(forbidden_ids[:10])}] 等拆炸弹选项来助攻。\n"
                                        f"理由：拆炸弹会导致我方失去在这轮残局的防御力。宁可打高一点的碎牌，也绝不拆炸弹。"
                                    ))
                            else:
                                # 没有非拆炸弹的候选：提示"应优先自己完牌"
                                trigger_strategies.insert(0, (
                                    "【关键】放走队友警告：无合适牌型，应优先自己完牌",
                                    f"你想出与队友剩牌数一致的牌型（{teammate_left}张）来放走队友，"
                                    f"但当前所有候选都会**拆你的炸弹**。\n"
                                    f"**建议**：不要拆炸弹去迎合队友。应优先考虑：\n"
                                    f"1) 用其他牌型出牌，让队友后续接风。\n"
                                    f"2) 若你手牌数接近完牌（如≤5张），应优先自己完牌，给队友接风机会。"
                                ))
                                candidates = []  # 清空候选，避免错误推荐
                    
                    # 排序并展示候选
                    if candidates:
                        candidates = sorted(candidates, key=lambda m: rank_sort_key(m.get('rank')))
                        preview = []
                        rec_ids = []
                        for mv in candidates[:5]: # 增加展示数量
                            rlab = format_rank_value(mv.get('rank'))
                            if desired_type == 1:
                                preview.append(rlab)
                            elif desired_type == 2:
                                preview.append(f"对{rlab}")
                            else:
                                preview.append(f"三张{rlab}")
                            rec_ids.append(str(mv.get('id')))
                        
                        rec_ids_str = ", ".join(rec_ids)
                        trigger_strategies.append((
                            "【指令】放行首发推荐（严禁拆炸弹）",
                            f"为助攻队友，你**必须**在以下不拆炸弹的选项中选择：ID [{rec_ids_str}] (如 {', '.join(preview)})。\n"
                            f"**绝对禁令**：禁止选择任何描述中带有‘拆炸弹’字样的 ID（如 ID 3-7），即便其点数更小。保住本方炸弹的威慑力比减少1点点数更重要。"
                        ))
                    elif desired_type == 3:
                        # 队友剩3张但自己没有3张：建议打单张或对子顺带
                        trigger_strategies.append(TACTICS_DB["situational"]["teammate_3_no_triple"])

        # 残局首发提醒（基于推演轮次 + 对手剩牌）
        opp_max = None
        if remaining_counts and opponents:
            try:
                opp_max = max(int(remaining_counts.get(o, 0)) for o in opponents)
            except Exception:
                opp_max = None

        # === 残局三轮首发：优先出第二大牌 (1v1 或 1v2/2v2 有对手剩牌≤3) ===
        # 逻辑：倒数第三手，出最大可能被顶死然后送走；出太小控不住；出第二大（Second Nut）通常是最佳试探/逼迫手段。
        if (
            game_stage == "残局阶段"
            and optimization_turns == 3
            and is_leader
            and can_play_moves
            and remaining_counts
            and opponents
            # 去掉队友已游的限制，允许2v2触发
            # and teammate in (finished_players or []) 
        ):
            try:
                active_opponents = [
                    o for o in (opponents or [])
                    if (o not in (finished_players or [])) and int(remaining_counts.get(o, 0)) > 0
                ]
            except Exception:
                active_opponents = []

            # 判定触发条件：
            # 1. 1v1 (仅一个活跃对手)
            # 2. 或者 任意活跃对手手牌数 <= 3
            is_1v1 = (len(active_opponents) == 1)
            threatening_opps = [o for o in active_opponents if int(remaining_counts.get(o, 99)) <= 3]
            has_threatening_opp = len(threatening_opps) > 0

            if is_1v1 or has_threatening_opp:
                # 寻找可能的牌型
                candidates = [m for m in (can_play_moves or []) if m.get('type') not in (0, None)]
                
                # 获取危险对手的张数集合(用于避同)
                # 如果是1v1，取该对手张数（无论是否<=3都避同一手）
                # 如果非1v1，仅避那些<=3张的对手
                avoid_counts = set()
                if is_1v1:
                    try:
                        c = int(remaining_counts.get(active_opponents[0], -1))
                        if c > 0: avoid_counts.add(c)
                    except: pass
                else:
                    for o in threatening_opps:
                        try:
                            c = int(remaining_counts.get(o, -1))
                            if c > 0: avoid_counts.add(c)
                        except: pass
                
                avoid_msg = f"（需避张数：{', '.join(map(str, avoid_counts))}）" if avoid_counts else ""

                # === 修正逻辑：跨牌型混合比较，寻找“绝对牌点”的第二大 ===
                # 排序键：(是否炸弹/王, Rank值, 牌张数)
                def _mixed_sort_key(m):
                    mt = m.get('type') or 0
                    rk = normalize_rank_value(m.get('rank')) or 0
                    is_bomb = mt >= 30 or rk >= 20 
                    count = len(m.get('card_ids') or [])
                    return (1 if is_bomb else 0, rk, count)

                sorted_candidates = sorted(candidates, key=_mixed_sort_key, reverse=True)
                
                final_suggestion = None
                suggestion_reason = ""

                if len(sorted_candidates) >= 1:
                    max_move = sorted_candidates[0]
                    max_rank = normalize_rank_value(max_move.get('rank'))

                    # 1. 找出所有比最大牌Rank小的候选（真正的“第二梯队”）
                    # 如果全是同Rank（如只有555），则考虑拆出的更小牌型（如对5）
                    lower_tier = [m for m in sorted_candidates if normalize_rank_value(m.get('rank')) < max_rank]
                    
                    # 特殊情况：如果全是同一Rank（没有更小的Rank），则只能在同Rank里找（即拆最大牌）
                    if not lower_tier:
                        lower_tier = [m for m in sorted_candidates if normalize_rank_value(m.get('rank')) == max_rank and m != max_move]

                    if lower_tier:
                        # Natural Second Best (仅按牌力)
                        natural_2nd = lower_tier[0]
                        natural_2nd_rank = normalize_rank_value(natural_2nd.get('rank'))
                        
                        # 检查是否有张数冲突 (任意一个危险张数)
                        n2_count = len(natural_2nd.get('card_ids') or [])
                        
                        if avoid_counts and n2_count in avoid_counts:
                            # 冲突！执行下级候选策略
                            
                            # 1. 优先尝试：找第三大牌（Rank更低且不冲突的）
                            third_tier = [m for m in lower_tier if (normalize_rank_value(m.get('rank')) or 0) < (natural_2nd_rank or 0)]
                            valid_thirds = [m for m in third_tier if (len(m.get('card_ids') or []) not in avoid_counts)]
                            
                            if valid_thirds:
                                final_suggestion = valid_thirds[0]
                                suggestion_reason = "（已避同：第二大冲突，改选第三大牌）"
                            else:
                                # 2. 其次尝试：拆开第二大牌（必须非顺子且不冲突）
                                # 过滤掉 type=5 (顺子)，因为顺子拆开通常不符合直觉
                                splits = [m for m in lower_tier 
                                          if normalize_rank_value(m.get('rank')) == natural_2nd_rank 
                                          and m.get('type') != 5
                                          and (len(m.get('card_ids') or []) not in avoid_counts)]
                                
                                if splits:
                                    final_suggestion = splits[0] # 取最强拆分
                                    suggestion_reason = "（已避同：拆开第二大牌）"
                                else:
                                    # 3. 兜底：如果第二、第三大牌及拆分都不能避同，则仍出第二大牌
                                    final_suggestion = natural_2nd
                                    suggestion_reason = "（均冲突，无奈仍选第二大）"
                        else:
                            # 无冲突，直接采纳
                            final_suggestion = natural_2nd
                            suggestion_reason = "（Second Best）"

                if final_suggestion:
                    def _desc(mv: dict) -> str:
                        return mv.get('desc') or f"type={mv.get('type')} rank={format_rank_value(mv.get('rank'))}"
                    
                    trigger_strategies.insert(0, (
                         "【最高优先】残局三轮出尽：首发优先出第二大牌",
                         f"你剩三轮牌，场上存在威胁{avoid_msg}。\n"
                         "为防止最大牌被管上导致失控，且防止送对手一手完牌，策略如下：\n"
                         f"1. 优先选**第二大**牌型；\n"
                         f"2. 若张数与对手相同（特别是剩牌少于3张的对手），则**拆开它**或选**第三大**。\n"
                         f"推荐候选：{_desc(final_suggestion)} {suggestion_reason}"
                    ))

        # 对手≤3张 + 你两轮出尽：首发避同张数（全场景）
        if remaining_counts and opponents and optimization_turns == 2 and can_play_moves:
            try:
                # 已提前提取 max_opp_cnt, all_opp_counts
                low_counts = {
                    c for c in all_opp_counts if c <= 3
                }
            except Exception:
                low_counts = set()

            if low_counts:
                avoid_same_count_active = True
                candidates = [m for m in (can_play_moves or []) if m.get('type') not in (0, None)]

                safe_candidates = [
                    m for m in candidates
                    if len(m.get('card_ids') or []) not in low_counts
                ]

                # [修正] 计划一致性：优先寻找能确保剩余一轮完牌的操作，即便它可能与对手张数冲突
                all_consistent = []
                for m in (candidates or []):
                    m_ids = set(m.get('card_ids') or [])
                    rem_hand = [cid for cid in optimization_hand if cid not in m_ids]
                    if not rem_hand:
                        all_consistent.append(m)
                        continue
                    try:
                        _, _, bundle_after = calculate_hand_optimization(
                            rem_hand,
                            return_all=True,
                            two_weight=two_weight
                        )
                        if int(bundle_after.get("best", {}).get("turns", 99)) == 1:
                            all_consistent.append(m)
                    except Exception:
                        pass

                plan_consistent_and_safe = [m for m in all_consistent if m in (safe_candidates or [])]
                
                is_following_plan = False
                is_successfully_avoiding = False # 记录是否成功实现了"避同"
                if all_consistent:
                    is_following_plan = True
                    if plan_consistent_and_safe:
                        # 既符合计划又避同，完美
                        safe_candidates = plan_consistent_and_safe
                        is_successfully_avoiding = True
                    else:
                        # 只有撞同才能保住最优计划：优先保计划（解决逻辑短视问题）
                        safe_candidates = all_consistent
                        # 注意：此时 is_successfully_avoiding 为 False

                def _desc(mv: dict) -> str:
                    return mv.get('desc') or f"type={mv.get('type')} rank={format_rank_value(mv.get('rank'))}"

                extra = ""
                if safe_candidates:
                    if is_following_plan:
                        # [安全性增强] 2V2无炸弹残局：如果手牌中唯一顶级控牌无炸弹保护，建议先出小牌试探
                        # 约束1：若存在“张数压制”牌（即张数 > 所有对手剩余张数），那是绝对必杀窗口，不采用先小后大。
                        # 约束2：仅在成功"避同"的前提下才采用先小后大策略；若必须撞同，则优先出大牌压制。
                        
                        # [修正启发式排序] 针对 2V2 或 2V1 场景：
                        # 在还有队友接风的情况下（not is_solo_struggle），若成功避同且有绝对大牌，则采取先小后大。
                        
                        has_suppression = (max_opp_cnt < 4) and any(len(m.get('card_ids', [])) > max_opp_cnt for m in safe_candidates if m.get('type', 0) < 20)

                        use_reverse = True # 默认大牌优先
                        if is_successfully_avoiding and not bombs and not is_solo_struggle and not has_suppression:
                            # 统计当前手牌中的顶级绝对控牌（基于已出牌记录推断）
                            boss_count = 0
                            if control_card_ready:
                                for lb in ["大王", "小王", "2", "A"]:
                                    if control_card_ready.get(lb): boss_count += 1
                            
                            # [慢打触发] 如果持有当前唯一绝对大牌且仅需两轮：先出另一手小牌/非绝对控牌，诱导出牌后用大牌夺回控权并掩护队友。
                            if boss_count >= 1:
                                use_reverse = False # 切换为先小后大排列
                        
                        safe_sorted = sorted(
                            safe_candidates,
                            key=lambda m: (
                                (len(m.get('card_ids', [])) > max_opp_cnt and m.get('type', 0) < 20 and max_opp_cnt < 4), # 张数压制优先 (bool)
                                rank_sort_key(m.get('rank')), 
                                len(m.get('card_ids') or [])
                            ),
                            reverse=use_reverse
                        )
                        extra = "\n优先推荐（最优组牌策略一致）：" + ", ".join(_desc(m) for m in safe_sorted[:5])
                    else:
                        # 不保计划时，依然优先张数压制
                        safe_sorted = sorted(
                            safe_candidates,
                            key=lambda m: (
                                (len(m.get('card_ids', [])) > max_opp_cnt and m.get('type', 0) < 20),
                                len(m.get('card_ids') or []), 
                                rank_sort_key(m.get('rank'))
                            ),
                            reverse=True
                        )
                        extra = "\n推荐候选（避同张数+压制）：" + ", ".join(_desc(m) for m in safe_sorted[:5])
                else:
                    same_sorted = sorted(
                        candidates,
                        key=lambda m: rank_sort_key(m.get('rank')),
                        reverse=True
                    )
                    extra = "\n必须出同张数时，优先最大牌候选：" + ", ".join(_desc(m) for m in same_sorted[:5])

                trigger_strategies.insert(
                    0,
                    (
                        TACTICS_DB["situational"]["endgame_two_turns_opp_leq3_avoid_same_count"][0],
                        TACTICS_DB["situational"]["endgame_two_turns_opp_leq3_avoid_same_count"][1] + extra
                    )
                )

        # 计算当前活跃对手数量 (2v2 vs 1v2 vs 1v1)
        active_opponents_count = 0
        if remaining_counts and opponents:
            try:
                active_opponents_count = sum(1 for o in opponents if (get_p_name(o) not in (finished_players or [])) and int(remaining_counts.get(get_p_name(o), 0)) > 0)
            except Exception:
                active_opponents_count = 0

        # 两轮出尽专项：首发 + 1v2/2v2 + 至少一名对手>3张，按固定优先序给出推荐
        if (
            game_stage == "残局阶段"
            and is_leader
            and optimization_turns == 2
            and active_opponents_count == 2
            and max_opp_cnt > 3
            and can_play_moves
        ):
            playable = [m for m in (can_play_moves or []) if m.get('type') not in (0, None)]

            opp_counts_set = set(c for c in all_opp_counts if c > 0)
            ready = control_card_ready or {}

            def _move_label(mv: dict) -> str:
                return mv.get('desc') or f"type={mv.get('type')} rank={format_rank_value(mv.get('rank'))}"

            def _same_type_gap_candidate(moves: list):
                by_type = {}
                for mv in moves:
                    mt = mv.get('type')
                    if mt in (0, None, 20, 21, 22):
                        continue
                    by_type.setdefault(mt, []).append(mv)

                picked = []
                for _, group in by_type.items():
                    if len(group) < 2:
                        continue
                    sorted_group = sorted(group, key=lambda m: rank_sort_key(m.get('rank')), reverse=True)
                    top = sorted_group[0]
                    second = sorted_group[1]
                    # “牌面数值差距较大”阈值：>=3
                    if rank_sort_key(top.get('rank')) - rank_sort_key(second.get('rank')) >= 3:
                        # 相同牌型且差距大：先出较小那手（高两手中的较小者）
                        picked.append(second)
                return picked

            def _is_control_single(mv: dict) -> bool:
                if (mv.get('type') or 0) != 1:
                    return False
                rv = normalize_rank_value(mv.get('rank'))
                if rv == 21:
                    return bool(ready.get("大王"))
                if rv == 20:
                    return bool(ready.get("小王"))
                if rv == 15:
                    return bool(ready.get("2"))
                if rv == 14:
                    return bool(ready.get("A"))
                return False

            suppression_moves = [
                m for m in playable
                if (m.get('type') or 0) < 20 and len(m.get('card_ids') or []) > max_opp_cnt
            ]

            control_singles = [m for m in playable if _is_control_single(m)]
            unique_control_only = len(control_singles) == 1
            unique_control_moves = control_singles[:] if unique_control_only else []

            absolute_control_moves = control_singles + [m for m in playable if (m.get('type') or 0) >= 20]

            avoid_same_count_moves = [
                m for m in playable
                if len(m.get('card_ids') or []) not in opp_counts_set
            ]

            same_type_gap_moves = _same_type_gap_candidate(playable)

            # 依优先序拼接推荐：
            # 1) 张数压制 -> 2) 唯一大牌控牌 -> 3) 绝对大牌控牌 -> 4) 张数避同 -> 5) 同型差距大先小
            ordered = []
            for bucket in (
                sorted(suppression_moves, key=lambda m: (len(m.get('card_ids') or []), rank_sort_key(m.get('rank'))), reverse=True),
                sorted(unique_control_moves, key=lambda m: rank_sort_key(m.get('rank')), reverse=True),
                sorted(absolute_control_moves, key=lambda m: ((m.get('type') or 0) >= 20, rank_sort_key(m.get('rank'))), reverse=True),
                sorted(avoid_same_count_moves, key=lambda m: rank_sort_key(m.get('rank')), reverse=True),
                sorted(same_type_gap_moves, key=lambda m: rank_sort_key(m.get('rank'))),
            ):
                for mv in bucket:
                    mid = mv.get('id')
                    if mid is not None and any(x.get('id') == mid for x in ordered):
                        continue
                    ordered.append(mv)

            # 兜底：补全剩余合法牌，避免推荐池为空
            for mv in sorted(playable, key=lambda m: rank_sort_key(m.get('rank')), reverse=True):
                mid = mv.get('id')
                if mid is not None and any(x.get('id') == mid for x in ordered):
                    continue
                ordered.append(mv)

            preview = ", ".join(_move_label(m) for m in ordered[:6]) if ordered else "无"
            trigger_strategies.insert(0, (
                "【最高优先】两轮首发(1v2/2v2)执行序",
                "满足条件：你仅需两轮出尽，且局面为1v2或2v2，并且至少一名对手剩牌>3。\n"
                "出牌优先序必须为：\n"
                "1) 张数压制（牌张数>对手最大剩牌）；\n"
                "2) 唯一大牌控牌；\n"
                "3) 绝对大牌控牌（2/王/炸弹）；\n"
                "4) 张数避同（避免与任一对手剩牌张数相同）；\n"
                "5) 相同牌型且牌面差距较大时先出小牌（中+小先小；大+中先中）。\n"
                f"当前推荐顺序（前6）：{preview}"
            ))

        # 触发条件：首发 + 推演出尽轮次严格等于对应值
        if game_stage == "残局阶段" and optimization_turns is not None and not avoid_same_count_active:
            # 判断是否为“孤军奋战”局面（队友已游出）
            # is_endgame_alone 在上方已计算为 teammate_finished

            if optimization_turns == 2:
                if teammate_finished:
                    trigger_strategies.append(TACTICS_DB["situational"]["endgame_last2_leader_1v1_1v2"])
                else:
                    # 2v2或2v1（队友在场）：强调先小后大、回控接风
                    trigger_strategies.append(TACTICS_DB["situational"]["endgame_last2_leader_2v2"])
                    
                    # [精准触发] 如果手中握有绝对大牌（2或王或炸弹），针对性引导慢打
                    # 同时也检查是否有“张数压制”机会
                    current_safe = [m for m in (can_play_moves or []) if m.get('type', 0) > 0]
                    has_suppression = any(len(m.get('card_ids', [])) > max_opp_cnt for m in current_safe if m.get('type', 0) < 20)
                    has_boss = any(is_big_single(parse_rank_from_card_id(cid)) for cid in (hand_cards or [])) or bool(bombs)
                    
                    if has_boss and is_leader and not has_suppression:
                         trigger_strategies.append(TACTICS_DB["situational"]["endgame_two_turns_boss_recover_lead"])

                # [追加场景] 对手剩牌多(>6张)时的炸弹慢打策略 (适用于首发/跟牌)
                # 注：如果是跟牌，会在后面 bomb 逻辑中处理，这里优先处理首发触发
                if bombs and max_opp_cnt > 6:
                    trigger_strategies.append(TACTICS_DB["situational"]["bomb_slow_play_opp_many"])
            
            if optimization_turns == 3:
                avoid_same_count_active = True # 设置标记，防止后续重复触发默认策略
                trigger_strategies.append(TACTICS_DB["situational"]["endgame_last3_leader_prompt"])

            if optimization_turns == 4:
                if teammate_finished:
                    trigger_strategies.append(TACTICS_DB["situational"]["endgame_last4_leader_split_3rd"])
                else:
                    trigger_strategies.append(TACTICS_DB["situational"]["endgame_last4_leader_prompt"])

        # 专项逻辑：对手剩4张且首发（规避无意义的组合压制，强化保护组牌）
        if is_leader and remaining_counts:
            # 检查是否有对手剩4张
            opp_has_4 = False
            for o in (opponents or []):
                if o not in (finished_players or []):
                    c = int(remaining_counts.get(o, 99))
                    if c == 4:
                        opp_has_4 = True
                        break
            if opp_has_4:
                # [精简提示] 仅当玩家有非单张组合牌（顺子、三张、连对等）时才提示规避逻辑
                has_combo = any(m.get('type') not in (1, 0, None, 20, 21, 22) for m in (can_play_moves or []))
                if has_combo:
                    trigger_strategies.append(TACTICS_DB["situational"]["avoid_lead_combo_when_opp_4"])

        # 专项逻辑：强控慢打（2+炸弹 且 拥有绝对Boss牌 且 还需要多轮）
        has_strong_bombs = len(bombs) >= 2
        is_any_boss = control_card_ready and (
            control_card_ready.get("大王") or 
            control_card_ready.get("小王") or 
            control_card_ready.get("2")
        )
        if is_leader and has_strong_bombs and is_any_boss and optimization_turns and optimization_turns >= 4:
            trigger_strategies.append(TACTICS_DB["situational"]["endgame_slow_play_with_strong_control"])

        # 专项逻辑：开局/中局强手控权 (多炸/有王)
        if is_leader and game_stage in ("开局阶段", "中局阶段") and has_strong_bombs and is_any_boss:
            trigger_strategies.append(TACTICS_DB["situational"]["opening_take_control_for_clearing"])
        
        # 专项逻辑：领跑慢探（Leader且碎牌较多，拥有部分卫士且非1v1）
        # 适用于 2v2/1v2/2v1 局面，且与是否有炸无关
        teammate_finished = (teammate in (finished_players or [])) if teammate else False
        is_1v1 = (active_opponents_count == 1 and teammate_finished)
        is_slow_play_scenario = (not is_1v1) and (active_opponents_count > 0)
        
        if (
            is_leader and 
            is_slow_play_scenario and 
            is_any_boss and 
            hand_structure
        ):
            singles_list = hand_structure.get('isolated_singles', [])
            small_singles_count = sum(1 for s in singles_list if normalize_rank_value(s) <= 12)
            if small_singles_count >= 2:
                trigger_strategies.append(TACTICS_DB["situational"]["leader_slow_play_with_splinters"])

        # [New] 三项新增首发/跟牌·红桃2战术
        if has_red_heart_2 and can_play_moves:
            fullhouse_moves = [mv for mv in (can_play_moves or []) if mv.get('type') == 4]
            rh2_update_suggested = False

            # [Add] 跟牌拦截优化：红桃2优先配手中第二大牌 (适用于对子/三张/三带二)
            if (not is_leader) and optimization_turns and optimization_turns <= 3 and last_move:
                lt = last_move.get('type')
                if lt in (2, 3, 4):
                    # 寻找使用了红桃2的跟牌选项
                    wildcard_follow_moves = [
                        m for m in can_play_moves 
                        if (m.get('type') == lt) and move_uses_red_heart_2(m)
                    ]
                    if len(wildcard_follow_moves) >= 2:
                        # 按点数从大到小排序，取第二大
                        sorted_wf = sorted(wildcard_follow_moves, key=lambda m: rank_sort_key(m.get('rank')), reverse=True)
                        second_best_wf = sorted_wf[1]
                        trigger_strategies.insert(0, (
                            TACTICS_DB["specials"]["red_heart_2"][4][0],
                            TACTICS_DB["specials"]["red_heart_2"][4][1] + f"\n推荐候选：{second_best_wf.get('desc')} [ID {second_best_wf.get('id')}]"
                        ))
            
            # 强化1：炸弹升张优先 (4张自然炸弹 + 红桃2 -> 5张)
            if is_leader and game_stage in ("开局阶段", "中局阶段"):
                # 获取手牌结构中 count=4 的点数 (自然炸弹)
                # hand_rank_counts 仅在 hand_cards 非空时于上方统计；此处兜底为空 dict，避免 UnboundLocalError
                try:
                    _hrc = hand_rank_counts.items()
                except UnboundLocalError:
                    _hrc = {}
                natural_4_ranks = [r for r, c in _hrc if c == 4]
                if natural_4_ranks:
                    trigger_strategies.insert(0, TACTICS_DB["situational"]["rh2_upgrade_to_five_bomb_priority"])
                    rh2_update_suggested = True

            if fullhouse_moves:
                # 触发1：红桃2三带二最大化
                wildcard_fhs = [mv for mv in fullhouse_moves if any(cid.startswith('H15') or '♥2' in cid or cid == 'H2' for cid in (mv.get('card_ids') or []))]
                if wildcard_fhs:
                    triplet_ranks = [normalize_rank_value(mv.get('rank')) for mv in wildcard_fhs]
                    if len(set(triplet_ranks)) > 1:
                        trigger_strategies.append(TACTICS_DB["situational"]["wildcard_fullhouse_max_triplet"])
                
                # 触发3：保留大2单张清理碎牌
                if game_stage in ("开局阶段", "中局阶段") and wildcard_fhs and not rh2_update_suggested:
                    # 检查是否有多个小碎牌
                    small_p_count = sum(1 for r in pairs if normalize_rank_value(r) <= 10)
                    small_s_count = sum(1 for r in singles if normalize_rank_value(r) <= 10)
                    if (small_p_count + small_s_count) >= 2:
                        trigger_strategies.append(TACTICS_DB["situational"]["rh2_keep_as_guard_single"])

        if is_leader and game_stage in ("开局阶段", "中局阶段"):
            # 触发2：弱炸慢打
            if isinstance(bombs, list) and len(bombs) == 1:
                b_rank = normalize_rank_value(bombs[0])
                if b_rank is not None and b_rank <= 6 and is_any_boss:
                    trigger_strategies.append(TACTICS_DB["situational"]["stealth_bomb_weak_bomb_slow_play"])

        leader_principles = []
        is_endgame_critical_finish = (optimization_turns is not None and optimization_turns <= 3)

        # === 修正：残局多轮次（>3轮）但在对手听牌（≤3张）时的首发策略 ===
        # 场景：自己还需要好几手（如5手），但对手只剩2张了。此时首发绝对不能直接甩最大牌（如小王），
        # 否则剩下的烂牌（4,8,9）完全失去保护，只能眼睁睁看对手走掉。
        # 应该出第二大牌（2）去顶大王，或者出中张求队友。
        if (
            game_stage == "残局阶段"
            and not is_endgame_critical_finish
            and is_leader
            and can_play_moves
            and remaining_counts
            and opponents
        ):
            # 识别危险对手
            threatening_opps = [
                o for o in (opponents or []) 
                if o not in (finished_players or []) 
                and 0 < int(remaining_counts.get(o, 99)) <= 3
            ]
            
            if threatening_opps:
                active_opponents = [o for o in (opponents or []) if o not in (finished_players or [])]
                is_1v1_situation = (len(active_opponents) == 1)
                
                # 寻找单张里的第二大逻辑
                candidates = [m for m in (can_play_moves or []) if m.get('type') == 1]
                # 简单按 Rank 排序
                sorted_singles = sorted(candidates, key=lambda m: rank_sort_key(m.get('rank')), reverse=True)
                
                rec_str = ""
                if len(sorted_singles) >= 2:
                    # 有至少两张单牌，最大的是 sorted_singles[0]
                    # 第二大的是 sorted_singles[1]
                    max_card = sorted_singles[0]
                    sec_card = sorted_singles[1]
                    
                    max_rank = normalize_rank_value(max_card.get('rank'))
                    sec_rank = normalize_rank_value(sec_card.get('rank'))
                    
                    # 检测是否为全局最大牌
                    is_global_max = False
                    if control_card_ready:
                        if max_rank == 21 and control_card_ready.get("大王"): is_global_max = True
                        elif max_rank == 20 and control_card_ready.get("小王"): is_global_max = True
                        elif max_rank == 15 and control_card_ready.get("2"): is_global_max = True
                        elif max_rank == 14 and control_card_ready.get("A"): is_global_max = True

                    # 只有当两张牌不是同一种牌（避免“不要出2优先出2”的笑话）且最大牌是强牌时才有意义
                    if max_rank >= 15 and max_card.get('desc') != sec_card.get('desc'):
                        if is_global_max:
                            rec_str = f"\n推荐操作：你手中的**{max_card.get('desc')}**已是全场最大单张。可以直接出此牌控权，或先尝试出**{sec_card.get('desc')}**（第二大）观察对手反应。"
                        else:
                            rec_str = f"\n推荐操作：**不要出{max_card.get('desc')}**。优先出**{sec_card.get('desc')}**（第二大）进行试探。"
                    elif is_global_max:
                        rec_str = f"\n提示：你手里的**{max_card.get('desc')}**已具备绝对控牌权，建议根据完牌轮次谨慎选择出牌点机。"

                trigger_strategies.insert(0, (
                    "【关键】听牌防守策略：保留最大控制权",
                    f"你还需要{optimization_turns}轮完牌，但对手仅剩{int(remaining_counts.get(threatening_opps[0], 0))}张牌。\n"
                    "1. **严禁**首发绝对最大单张（如大小王），除非你能连续控死对手。\n"
                    "2. 若出最大牌后你只剩下无助的小牌，你会立刻输掉比赛。\n"
                    "3. **正确策略**：出第二大单张（逼出对手大王）或中张（交给队友）。\n"
                    "4. 将最大牌留着，专门拦截对手最后的过牌尝试。" + rec_str
                ))

        if game_stage != "残局阶段":
            leader_principles.append(TACTICS_DB["roles"]["leader"][0])
        else:
            teammate_still_playing = bool(teammate) and (teammate not in (finished_players or []))
            
            # 1. 基础原则：只要自己不是马上能完牌（<=3轮），就要先出小牌
            # 即使对手在4-6张，只要不是极度危险(<=3张)，我们也要优先清理废牌，而不是无脑控大牌
            opp_in_danger_zone = False
            try:
                opp_in_danger_zone = any(0 < int(remaining_counts.get(o, 99)) <= 3 for o in (opponents or []))
            except Exception:
                pass
            
            # 如果没有特别危险的对手(<=3) 或者自己还没到斩杀线，就应该慢打小牌
            if not is_endgame_critical_finish:
                leader_principles.append(TACTICS_DB["roles"]["leader"][0])
            
            # 2. 补充原则：如果自己已经很接近完牌(<=3)，但对手并未甚至威胁(>3)，且没有特殊避同限制，
            # 也可以考虑先出小牌清理，而不是上来就炸。(这部分逻辑其实包含在 endgame_last3_leader_prompt 里了)
            
            if teammate_still_playing and not is_endgame_critical_finish:
                leader_principles.append(TACTICS_DB["roles"]["leader"][2])
        
        trigger_strategies.extend(leader_principles)

        # 定义大单张检查：A(14), 2(15), 小王(20), 大王(21)
        def is_big_single_check(s): 
             val = normalize_rank_value(s)
             return val is not None and val >= 14

        # 无孤张首发策略：首发且isolated_singles为空时(即无单张)，提示用现成组合首发
        if not singles:
            trigger_strategies.append(TACTICS_DB["roles"]["leader"][1])
            only_big_singles = False
        else:
             # 有单张，但全是大的(A及以上)，也视为"无小孤张"
             only_big_singles = all(is_big_single_check(s) for s in singles)
             if only_big_singles:
                 trigger_strategies.append(TACTICS_DB["situational"]["leader_no_single"])

        if only_big_singles:
            # [Audit] 已在上文追加过 leader_no_single，此处仅追加候选提示
            suggestions = []
            if pairs:
                sorted_pairs = sorted(pairs, key=lambda r: rank_value_map.get(r, 99))
                pair_labels = [f"对{label_rank(r)}" for r in sorted_pairs]
                if pair_labels:
                    suggestions.append(f"最小对子：{', '.join(pair_labels[:4])}")

            if triples:
                sorted_triples = sorted(triples, key=lambda r: rank_value_map.get(r, 99))
                triple_labels = [f"三张{label_rank(r)}" for r in sorted_triples]
                if triple_labels:
                    suggestions.append(f"可保留成三带二的三张：{', '.join(triple_labels[:3])}")

            if suggestions:
                trigger_strategies.append(("首发候选结构", "；".join(suggestions) + "。优先从这些结构里选最小的，保持牌型完整。"))
    else:
        trigger_strategies.extend(TACTICS_DB["roles"]["follow"])

        # [New] 对手≤3张时的强夺权拦截逻辑
        try:
             def _get_cnt(p):
                 return int(remaining_counts.get(get_p_name(p), 99))
             any_opp_low = any(0 < _get_cnt(o) <= 3 for o in (opponents or []))
        except Exception:
             any_opp_low = False
            
        if any_opp_low:
             # 判断是否为严重威胁：面对对手出牌，或者面对队友出牌但张数与任何报单对手一致 (危！)
             is_critical_threat = not is_teammate_move 
             if is_teammate_move and last_move:
                 move_count = len(last_move.get('card_ids') or [])
                 for opp in (opponents or []):
                     opp_count = _get_cnt(opp)
                     # 对手剩1-3张且出牌张数恰好匹配，是严重威胁
                     if 1 <= opp_count <= 3 and opp_count == move_count:
                         is_critical_threat = True
                         break
             
             if is_critical_threat:
                  trigger_strategies.append(TACTICS_DB["situational"]["endgame_take_control_with_max_card_when_opp_low"])
             elif is_teammate_move:
                  trigger_strategies.append(TACTICS_DB["situational"]["teammate_move_opp_reported_safe_release"])

        # === 1v1 且两轮完牌：跟牌必须用最大牌（拆牌不增轮次） ===
        if (
            game_stage == "残局阶段"
            and optimization_turns == 2
            and last_move
            and remaining_counts
            and opponents
            and teammate in (finished_players or [])
        ):
            try:
                active_opponents = [
                    o for o in (opponents or [])
                    if (o not in (finished_players or [])) and int(remaining_counts.get(o, 0)) > 0
                ]
            except Exception:
                active_opponents = []

            if len(active_opponents) == 1 and last_move.get('player') in (opponents or []):
                lm_type = last_move.get('type')
                target_len = len(last_move.get('card_ids') or [])
                follow_candidates = [
                    m for m in (can_play_moves or [])
                    if m.get('type') == lm_type
                    and len(m.get('card_ids') or []) == target_len
                    and beats_rank(m.get('rank'), last_move.get('rank'))
                ]

                if follow_candidates and (hand_card_ids or hand_cards):
                    pass_turns = None
                    try:
                        _, _, bundle_pass = calculate_hand_optimization(
                            hand_card_ids or hand_cards,
                            return_all=True,
                            two_weight=_two_weight_by_stage(game_stage)
                        )
                        pass_turns = bundle_pass.get("best", {}).get("turns")
                    except Exception:
                        pass_turns = None

                    eligible = []
                    for mv in follow_candidates:
                        used = set(mv.get('card_ids') or [])
                        remaining = [cid for cid in (hand_card_ids or hand_cards) if cid not in used]
                        _, _, bundle_follow = calculate_hand_optimization(
                            remaining,
                            return_all=True,
                            two_weight=_two_weight_by_stage(game_stage)
                        )
                        follow_turns = bundle_follow.get("best", {}).get("turns")
                        if pass_turns is None or (follow_turns is not None and follow_turns <= pass_turns):
                            eligible.append(mv)

                    if eligible:
                        max_rank = max(eligible, key=lambda m: rank_sort_key(m.get('rank')))
                        max_rank_value = rank_sort_key(max_rank.get('rank'))
                        max_candidates = [m for m in eligible if rank_sort_key(m.get('rank')) == max_rank_value]

                        move_kind = {
                            1: "single",
                            2: "pair",
                            3: "triple",
                            4: "triple_pair",
                            5: "straight",
                            6: "consecutive_pairs",
                            7: "plate",
                        }.get(lm_type, "single")

                        trigger_strategies.insert(0, (
                            "【关键】1v1两轮完牌：跟牌必须用最大牌",
                            "当前1v1且你仅需两轮出尽。跟牌时必须用**最大牌**阻击；若需拆牌跟牌，则仅在拆后完牌轮次不增加时才允许。"
                            + describe_pad_moves(max_candidates, move_kind)
                        ))

        if not is_teammate_move:
            # === NEW: 炸弹豪门 - 富裕炸弹积极拦截 ===
            # 条件：开局>=4炸 或 中局>=3炸，且普通牌型完全管不住对手
            
            # 1. 确定炸弹数量门槛
            bomb_threshold = 99
            if game_stage == "开局阶段":
                bomb_threshold = 4
            elif game_stage == "中局阶段":
                bomb_threshold = 3
            
            has_enough_bombs = (isinstance(bombs, list) and len(bombs) >= bomb_threshold)

            if (
                has_enough_bombs
                and last_move
                and last_move.get('player') in (opponents or [])
            ):
                lm_type_chk = last_move.get('type') or 0
                lm_rank_chk = last_move.get('rank')
                
                # 2. 检查是否有"任何"普通牌型能管上 (Include both strong and weak normals)
                can_beat_with_normal = False
                
                # 遍历所有合法出牌
                for m in (can_play_moves or []):
                    mt = m.get('type') or 0
                    if mt >= 20: continue # 跳过炸弹/同花顺
                    
                    # 必须能管上 (同类型 + 牌力够)
                    if mt == lm_type_chk:
                        # 长度检查 (对顺子/连对等)
                        if len(m.get('card_ids') or []) != len(last_move.get('card_ids') or []):
                            continue
                            
                        if beats_rank(m.get('rank'), lm_rank_chk):
                           can_beat_with_normal = True
                           break
                
                # 只有当普通牌完全管不住(can_beat_with_normal = False)时，才强力建议炸
                # 避免出现"能跟却不跟，非要炸"的过度应激
                if not can_beat_with_normal:
                     trigger_strategies.insert(0, (
                        "【炸弹豪门】富裕炸弹积极拦截",
                        f"当前手握 {len(bombs)} 组炸弹（满足{game_stage}≥{bomb_threshold}组），火力充足。"
                        "既然普通牌型管不住，**建议果断使用炸弹拦截**！"
                        "防止对手走掉，夺回牌权掌控局面。"
                    ))

            pad_prompt_added = False
            my_singles = [m for m in can_play_moves if m['type'] == 1]

            # === NEW: 优先用大孤张跟牌，避免拆对 ===
            # Case: 开局/中局 + 对手出小单张 + 我有大孤张可管(A/2/王) + 同时有拆对/三张的选项
            # 策略：Prioritize Isolated Big Single > Splitting Pair
            if (
                game_stage in ["开局阶段", "中局阶段"]
                and last_move
                and last_move.get('type') == 1
                and last_move.get('player') in (opponents or [])
            ):
                # 1. 识别手里的大孤张 (Rank >= 14, i.e., A, 2, Joker)
                # singles set contains rank strings of *isolated* singles
                big_isolated_ranks = set()
                for s_str in singles:
                    val = normalize_rank_value(s_str)
                    if val is not None and val >= 14:
                        big_isolated_ranks.add(val)

                # 2. 检查是否有合法的 split pair/triple 选项
                # 判断逻辑：Move是单张 Type=1，且其 rank 不在 isolated singles 列表中
                split_moves = []
                last_rank_val = normalize_rank_value(last_move.get('rank')) or 0
                
                for mv in (can_play_moves or []):
                    if mv.get('type') != 1: continue
                    mv_rank_val = normalize_rank_value(mv.get('rank'))
                    if mv_rank_val is None: continue
                    
                    # 必须能管上
                    if beats_rank(mv.get('rank'), last_move.get('rank')):
                        # 检查是否为拆牌：如果该rank没有在 singles 集合中出现，说明它是非孤张(来自对子/三张)
                        # 注意：singles 存的是 string rank.
                        is_isolated = False
                        for s_str in singles:
                            if normalize_rank_value(s_str) == mv_rank_val:
                                is_isolated = True
                                break
                        if not is_isolated:
                            split_moves.append(mv)

                # 3. 检查是否有能管上的大孤张
                valid_big_isolated = []
                for b_val in big_isolated_ranks:
                    if beats_rank(b_val, last_move.get('rank')):
                        valid_big_isolated.append(b_val)
                
                # 4. 触发建议：若有大孤张可用 且 存在拆牌选项
                if valid_big_isolated and split_moves:
                    # 格式化展示
                    readable_iso = [label_rank(r) for r in sorted(valid_big_isolated, reverse=True)]
                    readable_iso_str = ", ".join(readable_iso[:3])
                    readable_split = [label_rank(mv.get('rank')) for mv in split_moves[:3]]
                    readable_split_str = ", ".join(readable_split)
                    
                    trigger_strategies.insert(0, (
                        "【关键】跟牌优先用大孤张（勿拆结构）",
                        f"对手出单张，你手里有大孤张（{readable_iso_str}）可以直接管上。"
                        f"**强烈建议**：优先用大孤张顶牌/控牌，而不是拆开对子/三张（如单{readable_split_str}）去跟。"
                        "拆牌虽可能保留大牌，但往往增加完牌轮次并破坏结构，不如直接用大牌控权或逼出对手炸弹。"
                    ))

            # 开局/中局：跟牌若需拆结构，要求“跟牌后轮次下降”
            if (
                game_stage in ["开局阶段", "中局阶段"]
                and last_move
                and last_move.get('player') in (opponents or [])
                and (hand_card_ids or hand_cards)
            ):
                target_type = last_move.get('type')
                target_len = len(last_move.get('card_ids') or [])
                follow_candidates = [
                    m for m in (can_play_moves or [])
                    if m.get('type') == target_type
                    and len(m.get('card_ids') or []) == target_len
                    and beats_rank(m.get('rank'), last_move.get('rank'))
                ]

                # 识别同花顺牌组，用于避免“拆同花顺”
                sf_sets = []
                for mv in (can_play_moves or []):
                    if (mv.get('type') or 0) == 30:
                        ids = mv.get('card_ids') or []
                        if ids:
                            sf_sets.append(set(ids))

                def _splits_key_resource(mv: dict) -> bool:
                    if not mv:
                        return False
                    # 拆自然炸弹点数
                    if _move_splits_structure(mv):
                        return True
                    # 拆同花顺
                    if sf_sets:
                        mv_ids = set(mv.get('card_ids') or [])
                        if mv_ids and any(mv_ids & sf for sf in sf_sets):
                            return True
                    return False

                split_candidates = [m for m in follow_candidates if _move_splits_structure(m)]
                if split_candidates:
                    optimization_hand = hand_card_ids or hand_cards

                    pass_turns = None
                    try:
                        _, _, bundle_pass = calculate_hand_optimization(
                            optimization_hand,
                            return_all=True,
                            two_weight=_two_weight_by_stage(game_stage)
                        )
                        pass_turns = bundle_pass.get("best", {}).get("turns")
                    except Exception:
                        pass_turns = None

                    follow_scores = []
                    for mv in split_candidates:
                        used = set(mv.get('card_ids') or [])
                        remaining = [cid for cid in (optimization_hand or []) if cid not in used]
                        _, _, bundle_follow = calculate_hand_optimization(
                            remaining,
                            return_all=True,
                            two_weight=_two_weight_by_stage(game_stage)
                        )
                        follow_turns = bundle_follow.get("best", {}).get("turns")
                        follow_small = bundle_follow.get("best", {}).get("small_singles")
                        splits_key = _splits_key_resource(mv)
                        follow_scores.append((follow_turns, follow_small, splits_key, mv))

                    # 给出“拆哪种牌型更优”的递归评估（优先最少轮次 + 更少小孤张 + 不拆炸弹/同花顺）
                    ranked = sorted(
                        [item for item in follow_scores if item[0] is not None],
                        key=lambda x: (x[0], x[1] if x[1] is not None else 99, 1 if x[2] else 0)
                    )
                    
                    min_follow_turns = min((t for t, _, __, ___ in follow_scores if t is not None), default=None)

                    merged_title = "【关键】跟牌拆牌决策（PASS vs 拆牌候选）"
                    merged_desc_lines = []

                    # 1. 比较 PASS vs 拆牌轮次
                    is_worse_than_pass = False
                    
                    # [Tactical Update] 检查是否值得通过拆牌争夺首发 (Initiative Bonus)
                    rival_rank = normalize_rank_value(last_move.get('rank')) or 0
                    is_high_threat_single = (last_move.get('type') == 1 and rival_rank >= 13)
                    
                    # 统计我的强控资源
                    my_bombs_count = len(bombs or []) + len(straight_flushes or [])
                    # 统计 2 的数量
                    my_2s_count = 0
                    if hand_card_ids:
                        my_2s_count = sum(1 for cid in hand_card_ids if (str(cid).upper().endswith("15") or "-15" in str(cid) or str(cid).endswith("-2")))
                    elif hand_cards:
                        for c in hand_cards:
                            rv_c = normalize_rank_value(getattr(c, 'rank', ''))
                            if rv_c == 15:
                                my_2s_count += 1
                                
                    # 检查是否有小牌可出 (Rank < 10)
                    has_small_trash = False
                    if hand_card_ids:
                        for cid in hand_card_ids:
                            try:
                                parts = str(cid).split('-')
                                if len(parts) >= 2 and parts[1].isdigit():
                                    if int(parts[1]) < 10:
                                        has_small_trash = True
                                        break
                            except: pass
                    elif hand_cards:
                        has_small_trash = any(normalize_rank_value(getattr(c, 'rank', '')) < 10 for c in (hand_cards or []))

                    has_initiative_power = (my_bombs_count >= 1 or my_2s_count >= 2)
                    
                    if pass_turns is not None and min_follow_turns is not None and min_follow_turns > pass_turns:
                        is_worse_than_pass = True
                        
                        # 特例：如果是中早期，且对手出大单张，且我有抢权资本
                        if game_stage in ("opening", "mid_game") and is_high_threat_single and has_initiative_power and has_small_trash:
                            is_worse_than_pass = False # 不标记为 worse，从而不触发强力 PASS 警告
                            
                            line = f"虽然当前PASS预计{pass_turns}轮，拆牌会增加至{min_follow_turns}轮，但对手单张{format_rank_value(rival_rank)}较大。你握有{'炸弹' if my_bombs_count > 0 else '多张2'}，建议拆牌拦截夺回首发出牌权，优先清理手中多余孤张。"
                            merged_desc_lines.append(f"- **主动拦截建议**：{line}")
                        else:
                            _, base_desc = TACTICS_DB["situational"]["follow_split_only_if_improve_rounds"]
                            preview_worse = ", ".join(mv.get('desc') or "" for _, _, __, mv in follow_scores[:4])
                            line = f"当前PASS预计{pass_turns}轮，拆牌跟牌最优约{min_follow_turns}轮。{base_desc}"
                            if preview_worse:
                                line += f" (拆牌候选: {preview_worse})"
                            merged_desc_lines.append(f"- **PASS建议**：{line}")

                    # 2. 推荐最优拆牌候选
                    if ranked:
                        best_turns, best_small, best_splits_key, best_mv = ranked[0]
                        safe_ranked = [it for it in ranked if not it[2]]
                        best_list = safe_ranked[:3] if safe_ranked else ranked[:3]
                        preview_best = ", ".join(mv.get('desc') or "" for _, __, ___, mv in best_list)
                        
                        line = f"若必须跟牌，推荐候选（最少轮次优先）：{preview_best}。"
                        if best_splits_key:
                            line += " 注：此最优方案涉及拆炸弹/同花顺，若非绝杀请慎重。"
                        merged_desc_lines.append(f"- **拆牌优选**：{line}")

                    if merged_desc_lines:
                        trigger_strategies.append((merged_title, "\n".join(merged_desc_lines)))


                    # 必须接管：优先拆大牌且不增加轮次
                    if teammate_passed_after_opponent and pass_turns is not None:
                        big_control = []
                        for t, _, __, mv in follow_scores:
                            rv = normalize_rank_value(mv.get('rank'))
                            if t is None or t > pass_turns:
                                continue
                            if (mv.get('type') or 0) >= 20 or (rv is not None and rv >= 15):
                                big_control.append(mv)
                        if big_control:
                            title, desc = TACTICS_DB["situational"]["follow_must_take_over_split_big_no_more_rounds"]
                            preview = ", ".join(mv.get('desc') or "" for mv in big_control[:4])
                            extra = f" 可选示例：{preview}。" if preview else ""
                            trigger_strategies.append((title, desc + extra))

            # === NEW: 缺乏顶级控牌资源时主动抓接管机会 ===
            # 触发条件：炸弹数量≤1 且 缺少对2/对王
            low_top_control = False
            if hand_structure:
                bomb_count = len(hand_structure.get('bombs', []))
                has_pair_2_joker = any(r in ['2', '小王', '大王'] for r in pairs_rank_values)
                if bomb_count <= 1 and not has_pair_2_joker:
                    low_top_control = True

            # 只有在能跟牌（存在可用的普通牌型）且对手出牌时才提示
            if low_top_control and last_move and last_move.get('player') in opponents:
                lm_type = last_move.get('type')
                # 检查是否有普通牌型可跟（非炸弹）
                can_follow_with_normal = any(
                    m.get('type') == lm_type and m.get('type') < 20
                    for m in can_play_moves
                    if beats_rank(m.get('rank'), last_move.get('rank'))
                )
                if can_follow_with_normal:
                    trigger_strategies.append(TACTICS_DB["situational"]["take_control_with_normal_when_no_top"])
                    # 列举可跟的候选牌型（前几个示例）
                    follow_candidates = [
                        m for m in can_play_moves
                        if m.get('type') == lm_type and m.get('type') < 20
                        and beats_rank(m.get('rank'), last_move.get('rank'))
                    ]
                    if follow_candidates:
                        sorted_candidates = sorted(
                            follow_candidates,
                            key=lambda m: (rank_sort_key(m.get('rank')), len(m.get('card_ids') or []))
                        )
                        preview = []
                        for mv in sorted_candidates[:5]:
                            rlab = format_rank_value(mv.get('rank'))
                            mvtype = mv.get('type')
                            if mvtype == 1:
                                preview.append(rlab)
                            elif mvtype == 2:
                                preview.append(f"对{rlab}")
                            elif mvtype == 3:
                                preview.append(f"三张{rlab}")
                            elif mvtype == 4:
                                preview.append(f"{rlab}+X")
                            elif mvtype == 5:
                                preview.append(f"{rlab}起顺子")
                            elif mvtype == 6:
                                preview.append(f"{rlab}起连对")
                            elif mvtype == 7:
                                preview.append(f"{rlab}钢板")
                            else:
                                preview.append(f"{rlab}")
                        trigger_strategies.append((
                            "可接管候选",
                            f"可用普通牌型接管：{', '.join(preview)}" + (f"（共{len(follow_candidates)}种）" if len(follow_candidates) > len(preview) else "")
                        ))

            # [优化] 移除通用的队友PASS提醒，改为由单张/对子逻辑触发更具体的建议
            # if teammate_passed_after_opponent:
            #     trigger_strategies.append(TACTICS_DB["situational"]["control"])

            if game_stage in ["开局阶段", "中局阶段"] and teammate_is_leader and last_move and last_move.get('player') in opponents:
                trigger_strategies.append(TACTICS_DB["situational"]["pass_strategy_2"])

            if game_stage == "开局阶段":
                trigger_strategies.append(TACTICS_DB["situational"]["opening_follow_guard"])

                if singles:
                    unique_singles = sorted({str(r) for r in singles}, key=rank_sort_key)
                    readable = ", ".join(label_rank(r) for r in unique_singles[:6])
                    trigger_strategies.append(("开局孤张清理优先", f"当前孤张可以直接顶牌：{readable}。先用这些孤张清理，再考虑其他结构，避免开局拆对子或三张。"))
                elif pairs or triples or bombs_list:
                    caution_parts = []
                    if pairs:
                        sorted_pairs = sorted({str(r) for r in pairs}, key=rank_sort_key)
                        pair_labels = [f"对{label_rank(r)}" for r in sorted_pairs[:4]]
                        if pair_labels:
                            caution_parts.append("、".join(pair_labels))
                    if triples:
                        sorted_triples = sorted({str(r) for r in triples}, key=rank_sort_key)
                        triple_labels = [f"三张{label_rank(r)}" for r in sorted_triples[:3]]
                        if triple_labels:
                            caution_parts.append("、".join(triple_labels))
                    if bombs_list:
                        sorted_bombs = sorted({str(r) for r in bombs_list}, key=rank_sort_key)
                        bomb_labels = [f"炸弹{label_rank(r)}" for r in sorted_bombs[:2]]
                        if bomb_labels:
                            caution_parts.append("、".join(bomb_labels))

                    if caution_parts:
                        summary = "；".join(caution_parts)
                        trigger_strategies.append(("开局PASS优先提醒", f"目前只有 {summary} 这类结构可以拆来跟牌。除非必须拦截对手，否则尽量PASS或等队友出牌，别在开局拆关键结构。"))

            if last_move and last_move.get('player') in opponents and game_stage in ["开局阶段", "中局阶段"]:
                # 连对（例如三连对）：优先用“不拆三张”的现成连对跟牌夺首发
                target_cp_ranks = parse_consecutive_pairs_ranks(last_move)
                if target_cp_ranks:
                    target_pair_cnt = len(target_cp_ranks)
                    target_max = max(target_cp_ranks)

                    candidate_cp = []
                    candidate_cp_no_split = []
                    for mv in (can_play_moves or []):
                        my_ranks = parse_consecutive_pairs_ranks(mv)
                        if not my_ranks or len(my_ranks) != target_pair_cnt:
                            continue
                        if max(my_ranks) <= target_max:
                            continue
                        candidate_cp.append(mv)

                        # 不拆三张：连对点数不要占用任何三张点数（例如 888991010JJ 的三连对应选 99TTJJ，不选 88 99 TT）
                        if not (set(my_ranks) & (triples_rank_values or set())):
                            candidate_cp_no_split.append(mv)

                    if candidate_cp_no_split:
                        flow_strategies.append(
                            build_pad_entry("pad_strategy_consecutive_pairs", candidate_cp_no_split, "consecutive_pairs")
                        )
                        pad_prompt_added = True
                        suppress_pass_strategy = True

                lm_type = last_move.get('type')
                lm_rank = last_move.get('rank', 0)
                target_len = len(last_move.get('card_ids') or [])

                if lm_type == 1 and is_small_single(lm_rank) and singles_rank_values:
                    candidate_moves = [
                        m for m in my_singles
                        if beats_rank(m.get('rank'), lm_rank)
                    ]
                    candidate_moves = filter_by_structure(candidate_moves, singles_rank_values)
                    if candidate_moves:
                        flow_strategies.append(build_pad_entry("pad_strategy_single", candidate_moves, "single"))
                        pad_prompt_added = True
                elif lm_type == 2 and is_small_combo(lm_type, lm_rank) and pairs_rank_values:
                    candidate_pairs = [
                        m for m in can_play_moves
                        if m['type'] == 2
                        and len(m.get('card_ids', [])) == target_len
                        and beats_rank(m.get('rank'), lm_rank)
                    ]
                    candidate_pairs = filter_by_structure(candidate_pairs, pairs_rank_values)
                    if candidate_pairs:
                        # 若该对子属于三连对潜力(>=3 连续对子)则尽量别用它去跟小对子，优先推荐不破坏连对的对子
                        preserve_candidates = [
                            m for m in candidate_pairs
                            if normalize_rank_value(m.get('rank')) not in (pair_run_ranks or set())
                        ]
                        if preserve_candidates:
                            flow_strategies.append(build_pad_entry("pad_strategy_pair_preserve_consecutive", preserve_candidates, "pair"))
                        else:
                            flow_strategies.append(build_pad_entry("pad_strategy_pair", candidate_pairs, "pair"))
                        pad_prompt_added = True
                elif lm_type == 3 and is_small_combo(lm_type, lm_rank) and triples_rank_values:
                    candidate_triples = [
                        m for m in can_play_moves
                        if m['type'] == 3
                        and len(m.get('card_ids', [])) == target_len
                        and beats_rank(m.get('rank'), lm_rank)
                    ]
                    candidate_triples = filter_by_structure(candidate_triples, triples_rank_values)
                    if candidate_triples:
                        # 按"不新增单张"优先排序
                        low_loss, high_loss = rank_follow_moves_by_structure_loss(
                            hand_cards, candidate_triples, triples_rank_values
                        )
                        if low_loss and low_loss != candidate_triples:
                            flow_strategies.append((
                                "【高优先】顺牌-三张（优先保结构）",
                                "当前可跟三张：优先选择**不破坏顺子/连对/钢板结构、不新增单张**的候选。"
                                + describe_pad_moves(low_loss, "triple")
                                + " 这些候选打完后不会显著增加后续轮次。"
                            ))
                        else:
                            flow_strategies.append(build_pad_entry("pad_strategy_triple", candidate_triples, "triple"))
                        pad_prompt_added = True
                elif lm_type == 4 and is_small_combo(lm_type, lm_rank) and triples_rank_values:
                    candidate_fullhouse = [
                        m for m in can_play_moves
                        if m['type'] == 4
                        and len(m.get('card_ids', [])) == target_len
                        and beats_rank(m.get('rank'), lm_rank)
                    ]
                    candidate_fullhouse = filter_by_structure(candidate_fullhouse, triples_rank_values)
                    if candidate_fullhouse:
                        # 按"不新增单张"优先排序
                        low_loss, high_loss = rank_follow_moves_by_structure_loss(
                            hand_cards, candidate_fullhouse, triples_rank_values
                        )
                        if low_loss and low_loss != candidate_fullhouse:
                            flow_strategies.append((
                                "【高优先】顺牌-三带二（优先保结构）",
                                "当前可跟三带二：优先选择**不破坏顺子/连对结构、不新增单张**的候选。"
                                + describe_pad_moves(low_loss, "triple_pair")
                                + " 这些候选打完后不会显著增加后续轮次。"
                            ))
                        else:
                            flow_strategies.append(build_pad_entry("pad_strategy_triple_pair", candidate_fullhouse, "triple_pair"))
                        pad_prompt_added = True
                elif lm_type == 5 and is_small_combo(lm_type, lm_rank):
                    candidate_straights = [
                        m for m in can_play_moves
                        if m['type'] == 5
                        and len(m.get('card_ids', [])) == target_len
                        and beats_rank(m.get('rank'), lm_rank)
                    ]
                    if candidate_straights:
                        # 按"不新增单张"优先排序
                        low_loss, high_loss = rank_follow_moves_by_structure_loss(
                            hand_cards, candidate_straights, triples_rank_values
                        )
                        if low_loss and low_loss != candidate_straights:
                            flow_strategies.append((
                                "【高优先】顺牌-顺子（优先保结构）",
                                "当前可跟顺子：优先选择**不破坏更大结构（如更长顺子/连对/钢板）、不新增单张**的候选。"
                                + describe_pad_moves(low_loss, "straight")
                                + " 这些候选打完后不会显著增加后续轮次。"
                            ))
                        else:
                            flow_strategies.append(build_pad_entry("pad_strategy_straight", candidate_straights, "straight"))
                        pad_prompt_added = True
                elif lm_type == 7:
                    candidate_plates = [
                        m for m in can_play_moves
                        if m.get('type') == 7
                        and len(m.get('card_ids', [])) == target_len
                        and beats_rank(m.get('rank'), lm_rank)
                    ]
                    if candidate_plates:
                        base_turns = None
                        try:
                            _, _, base_bundle = calculate_hand_optimization(
                                optimization_hand,
                                return_all=True,
                                two_weight=_two_weight_by_stage(game_stage)
                            )
                            base_turns = (base_bundle.get("best", {}) or {}).get("turns")
                            base_turns = int(base_turns) if base_turns is not None else None
                        except Exception:
                            base_turns = None

                        preferred_plates = []
                        for mv in candidate_plates:
                            used = set(mv.get('card_ids') or [])
                            remaining = [cid for cid in (optimization_hand or []) if cid not in used]
                            try:
                                _, _, follow_bundle = calculate_hand_optimization(
                                    remaining,
                                    return_all=True,
                                    two_weight=_two_weight_by_stage(game_stage)
                                )
                                follow_best = follow_bundle.get("best", {}) or {}
                                follow_turns = follow_best.get("turns")
                                follow_turns = int(follow_turns) if follow_turns is not None else None
                                has_takeover_resource = (
                                    int(follow_best.get("sf_count", 0) or 0)
                                    + int(follow_best.get("bomb_4_5", 0) or 0)
                                    + int(follow_best.get("bomb_6_plus", 0) or 0)
                                ) > 0
                                turns_unchanged = (
                                    base_turns is not None
                                    and follow_turns is not None
                                    and (follow_turns + 1 == base_turns)
                                )
                                if turns_unchanged and has_takeover_resource:
                                    preferred_plates.append(mv)
                            except Exception:
                                continue

                        if preferred_plates:
                            flow_strategies.insert(0, build_pad_entry("pad_strategy_plate_preserve_takeover", preferred_plates, "plate"))
                            suppress_pass_strategy = True
                            pad_prompt_added = True
                        else:
                            flow_strategies.append((
                                "【高优先】顺牌-钢板（优先保结构）",
                                "当对手打出钢板且你有现成钢板可跟时，优先选择不新增小孤张、且不增加完牌轮次的钢板候选。"
                                + describe_plate_moves(candidate_plates)
                            ))
                            pad_prompt_added = True

            if should_prompt_flow and not pad_prompt_added:
                flow_strategies.append(TACTICS_DB["situational"]["pad_strategy_generic"])

            if last_move and last_move['type'] == 1:
                has_small_single = any(m['rank'] < 10 for m in my_singles)

                is_opponent_single = (last_move.get('player') in (opponents or []))
                if is_opponent_single:
                    lm_rank = last_move.get('rank', 0)
                    lm_value = normalize_rank_value(lm_rank)
                    candidate_follow_singles = [
                        m for m in my_singles
                        if beats_rank(m.get('rank'), lm_rank)
                    ]
                    can_follow_single = bool(candidate_follow_singles)

                    # === NEW: 残局对手≤3张时跟单张：强推荐最大单张，不要拆中等三张 ===
                    if game_stage == "残局阶段" and remaining_counts and can_follow_single:
                        try:
                            any_opp_low = any(int(remaining_counts.get(o, 99)) <= 3 for o in (opponents or []))
                        except Exception:
                            any_opp_low = False

                        if any_opp_low:
                            # 按"点数大→点数小"排序，优先推荐最大单张
                            sorted_by_rank_desc = sorted(
                                candidate_follow_singles,
                                key=lambda mv: rank_sort_key(mv.get('rank')),
                                reverse=True
                            )

                            # 取前3个最大单张作为推荐
                            max_singles = sorted_by_rank_desc[:3]
                            trigger_strategies.insert(
                                0,
                                build_pad_entry(
                                    "endgame_low_opp_follow_single_use_max",
                                    max_singles,
                                    "single"
                                )
                            )

                    # 对手出2或小王：若我方有对王，则优先拆对王用单王管上以争首发。
                    joker_takeover_applies = False
                    if can_follow_single and (lm_value in (15, 20)):
                        has_pair_small_joker = 20 in (pairs_rank_values or set())
                        has_pair_big_joker = 21 in (pairs_rank_values or set())

                        joker_follow_moves = [
                            m for m in candidate_follow_singles
                            if normalize_rank_value(m.get('rank')) in (20, 21)
                        ]
                        if lm_value == 15:
                            # 对手出2：小王/大王都能压；但只有有“对王”时才强调“拆对王”
                            if (has_pair_small_joker or has_pair_big_joker) and joker_follow_moves:
                                trigger_strategies.append(
                                    build_pad_entry("split_joker_pair_to_take_lead", joker_follow_moves, "single")
                                )
                                joker_takeover_applies = True
                        elif lm_value == 20:
                            # 对手出小王：必须用大王；要求手里有对大王，才提示“拆对大王”
                            big_joker_moves = [
                                m for m in joker_follow_moves
                                if normalize_rank_value(m.get('rank')) == 21
                            ]
                            if has_pair_big_joker and big_joker_moves:
                                trigger_strategies.append(
                                    build_pad_entry("split_joker_pair_to_take_lead", big_joker_moves, "single")
                                )
                                joker_takeover_applies = True

                    two_count = count_twos_in_hand()
                    has_two_stock = two_count >= 2

                    isolated_rank_values = set()
                    for s in singles:
                        normalized = normalize_rank_value(s)
                        if normalized is not None:
                            isolated_rank_values.add(normalized)

                    isolated_candidate_follow = [
                        m for m in candidate_follow_singles
                        if normalize_rank_value(m.get('rank')) in isolated_rank_values
                    ]
                    has_isolated_single_to_follow = bool(isolated_candidate_follow)
                    has_other_suitable_pad = any(
                        (not is_big_single(m.get('rank')))
                        for m in isolated_candidate_follow
                    )

                    need_control = bool(teammate_passed_after_opponent)
                    if (not need_control) and remaining_counts:
                        try:
                            need_control = any(remaining_counts.get(opp, 99) <= 3 for opp in opponents)
                        except Exception:
                            need_control = False

                    # [Update] 开局/中局若有2张以上2，积极拆2控单张
                    # 条件：对手打单张 + 我有2张以上2 + (无小单张可顺 OR 开局/中局阶段主动争控)
                    # 除非手牌本身完全无单牌结构（全是对/三/炸），否则都要提示拆2，而不是保守保留对2/三2/炸弹2。
                    should_active_split_2 = (
                        game_stage in ("开局阶段", "中局阶段")
                        and has_two_stock
                        and (not has_other_suitable_pad)
                    )

                    if can_follow_single and (should_active_split_2 or (has_two_stock and need_control)):
                        trigger_strategies.insert(0, TACTICS_DB["situational"]["split_twos_active_control"])
                        # 移除旧的 situational key 引用，使用新的强力提示
                        # trigger_strategies.append(TACTICS_DB["situational"]["pair_two_control"])

                        control_pair_labels = []
                        for p in pairs:
                            label = label_rank(p)
                            if label in ['2', '小王', '大王']:
                                control_pair_labels.append(label)

                        if control_pair_labels:
                            readable_pairs = ", ".join(
                                f"对{lbl}" for lbl in sorted(set(control_pair_labels), key=lambda x: rank_value_map.get(x, 99))
                            )
                            trigger_strategies.append(("对2/王牌拆牌顺序", f"当前可拆 {readable_pairs} 来顶上这一手。请先拆这些最大对子，再考虑其他结构或过牌保留三张、炸弹。"))

                    # 对手出大单张（A/2/王），且你有2/王可跟 -> 建议争控（无论队友是否PASS）
                    if can_follow_single and (not is_small_single(lm_rank)):
                        big_single_follow = [
                            m for m in (candidate_follow_singles or [])
                            if normalize_rank_value(m.get('rank')) is not None
                            and normalize_rank_value(m.get('rank')) >= 14
                        ]

                        # --- 判定是否为绝对大牌 (Boss Card) ---
                        # 利用 ai_client 传入的 control_card_ready (已计算了 "外界剩余" 情况)
                        has_boss_candidate = False
                        if control_card_ready:
                            for m in big_single_follow:
                                rv = normalize_rank_value(m.get('rank'))
                                label = None
                                if rv == 14: label = 'A'
                                elif rv == 15: label = '2'
                                elif rv == 20: label = '小王'
                                elif rv == 21: label = '大王'
                                
                                if label and control_card_ready.get(label):
                                    has_boss_candidate = True
                                    break
                        
                        if (
                            big_single_follow
                            and (
                                (game_stage in ("开局阶段", "中局阶段", "残局阶段") and len(big_single_follow) >= 1)
                                or has_boss_candidate
                            )
                        ):
                            # [优化] 如果队友已PASS，使用统一接管策略提示
                            if teammate_passed_after_opponent:
                                trigger_strategies.append(
                                    build_pad_entry(
                                        "teammate_pass_control_single",
                                        big_single_follow,
                                        "single",
                                        reverse=True
                                    )
                                )
                            else:
                                title = "对手大单张争控建议"
                                if has_boss_candidate:
                                    title += "（你有绝对大牌）"
                                trigger_strategies.insert(0, (
                                    title,
                                    TACTICS_DB["situational"]["teammate_pass_control_single"][1]
                                    + describe_pad_moves(big_single_follow, 'single', reverse=True)
                                ))

                    # 队友PASS后：对手小单张抢节奏，或你原是首发者但在对手跟牌后队友PASS -> 强提醒不要PASS，拆牌/上大牌争控
                    # [优化] 如果是自己首发的轮次，且队友PASS（没接住），则自己必须负起控场责任。
                    if teammate_passed_after_opponent and can_follow_single and (is_small_single(lm_rank) or is_self_round):
                        suppress_pass_strategy = True
                        
                        # === 最高优先级：队友PASS+对手抢权 -> 拆对/拆三/拆钢板顶牌 (优先大牌) ===
                        # 策略：对手出牌且队友PASS，必须积极拆牌阻击。
                        # 优先拆大对子(Q+)，保留单张控制力；若无大对则拆小对，哪怕是拆对4打4也要顶。
                        split_blocking_candidates = []
                        valid_source_ranks = (pairs_rank_values or set()) | (triples_rank_values or set())

                        for m in candidate_follow_singles:
                            rv = normalize_rank_value(m.get('rank'))
                            if rv in valid_source_ranks:
                                split_blocking_candidates.append((rv, m))
                        
                        # 排序：从大到小 (Big First) - [User Requirement]
                        split_blocking_candidates.sort(key=lambda x: x[0], reverse=True)

                        if split_blocking_candidates:
                            top_moves = [x[1] for x in split_blocking_candidates]
                            title = "【强制争控】队友PASS：拆解强牌接管"
                            if is_self_round:
                                title = "【关键】守住首发权：对手跟牌后队友PASS"
                            
                            trigger_strategies.insert(0, (
                                title,
                                f"当前{'是你首发的轮次，' if is_self_round else ''}对手出牌（{format_rank_value(lm_rank)}），队友已PASS，你不能放行！\n"
                                f"**策略**：优选拆开**点数更大的对子/三张**，这样被拆开的另一半后续仍有控制力。\n"
                                f"推荐方案（优先大牌拆分）：{describe_pad_moves(top_moves, 'single', reverse=True)}"
                            ))
                        else:
                            trigger_strategies.append(
                                build_pad_entry(
                                    "teammate_pass_control_single",
                                    candidate_follow_singles,
                                    "single",
                                    reverse=True
                                )
                            )

                if not singles and triples:
                    triple_labels = [f"三张{label_rank(t)}" for t in sorted({str(t) for t in triples}, key=rank_sort_key)]
                    readable_triples = "、".join(triple_labels[:4])
                    title, desc = TACTICS_DB["situational"]["triple_preserve_follow"]
                    trigger_strategies.append((title, f"{desc}\n当前涉及：{readable_triples}"))

                if not has_small_single and my_singles:
                    # 若触发“拆对王争首发”，不要再给出“PASS保大牌”的相反建议
                    if not joker_takeover_applies:
                        # 增加 is_self_round 的判定，确保在自己首发被顶且队友过牌时，不触发保守策略
                        if (teammate_passed_after_opponent or is_self_round) and last_move and last_move.get('type') == 1:
                            lm_rank = last_move.get('rank', 0)
                            if normalize_rank_value(lm_rank) is not None and normalize_rank_value(lm_rank) >= 14:
                                # 队友已PASS且对手出大单张（A/2/王），或原是自己轮次被重顶，此时必须争控，绝不建议PASS保大牌
                                pass
                            else:
                                trigger_strategies.append(TACTICS_DB["situational"]["hold_big_single"])
                        else:
                            trigger_strategies.append(TACTICS_DB["situational"]["hold_big_single"])

                if my_singles:
                    isolated_rank_values = set()
                    for s in singles:
                        if s in rank_value_map:
                            isolated_rank_values.add(rank_value_map[s])
                        else:
                            try:
                                isolated_rank_values.add(int(s))
                            except (TypeError, ValueError):
                                continue

                    isolated_follow_moves = [m for m in my_singles if m['rank'] in isolated_rank_values]

                    if isolated_follow_moves:
                        unique_ranks = sorted({m['rank'] for m in isolated_follow_moves})
                        readable = ", ".join(value_to_label.get(r, str(r)) for r in unique_ranks)
                        title, desc = TACTICS_DB["situational"]["single_priority"]
                        trigger_strategies.append((title, f"{desc}\n建议出牌：直接用孤张 {readable} 顶上，优先出其中最小的一张。"))
                    else:
                        trigger_strategies.append(TACTICS_DB["situational"]["split_guard_single"])

            # 队友PASS后：对手小对子抢节奏，但你能跟对子却没有现成对子可用 -> 强提醒不要PASS
            if (
                last_move
                and last_move.get('player') in (opponents or [])
                and last_move.get('type') == 2
                and teammate_passed_after_opponent
            ):
                lm_rank = last_move.get('rank', 0)
                if is_small_combo(2, lm_rank):
                    target_len = len(last_move.get('card_ids') or [])
                    candidate_pairs = [
                        m for m in can_play_moves
                        if m.get('type') == 2
                        and len(m.get('card_ids') or []) == target_len
                        and beats_rank(m.get('rank'), lm_rank)
                    ]
                    if candidate_pairs:
                        # [优化] 如果能在队友PASS后跟上对子，使用统一接管策略
                        suppress_pass_strategy = True
                        trigger_strategies.append(
                            build_pad_entry(
                                "teammate_pass_control_pair",
                                candidate_pairs,
                                "pair"
                            )
                        )

    # 2.3 特殊牌型与接风
    if has_red_heart_2:
        if red_heart_2_count == 1 and not bombs:
            # [Add] 针对单一红桃2且没炸弹的优化提示
            trigger_strategies.append(TACTICS_DB["situational"]["rh2_keep_as_guard_single"])
        else:
            trigger_strategies.append(TACTICS_DB["specials"]["red_heart_2"][0])
            
        if red_heart_2_count >= 2:
            trigger_strategies.append(TACTICS_DB["specials"]["red_heart_2"][1])
        
        # 残局升炸触发：手牌≤6张且持有红桃2时，若手上有可被红桃2升级的自然炸弹
        # （4张→5张 / 5张→6张，点数非王），用红桃2升炸能在【不增加完牌轮次】的
        # 前提下提升控牌能力，则触发“残局斩杀：红桃2优先用于最大炸弹”。
        if game_stage == "残局阶段" and has_red_heart_2 and hand_cards and len(hand_cards) <= 6:
            try:
                from collections import Counter
                rank_counts = Counter()
                for cid in hand_cards:
                    if isinstance(cid, str):
                        if cid.startswith('H15') or '♥2' in cid or cid == 'H2':
                            continue
                        if '-' in cid:
                            parts = cid.split('-')
                            if len(parts[0]) > 1:
                                try:
                                    rv = int(parts[0][1:])
                                    if rv < 20:  # 王不组普通炸
                                        rank_counts[rv] += 1
                                except ValueError:
                                    pass
                # 存在可被红桃2升级的自然炸弹（4张→5张，或5张→6张）
                upgradable = any(c >= 4 for c in rank_counts.values())
                if upgradable:
                    trigger_strategies.insert(0, TACTICS_DB["situational"]["endgame_kill_max_bomb_priority"])
            except Exception:
                pass

    if game_stage != "残局阶段" and (bombs or straight_flushes):
        trigger_strategies.extend(TACTICS_DB["specials"]["bombs"])

    if is_takeover:
        trigger_strategies.extend(TACTICS_DB["teammate"]["takeover"])

    # 跟牌时：若有红桃2且存在炸弹可控，则优先保留红桃2，不要分身去拼普通牌型
    # 跟牌时：若有红桃2并存在炸弹可控，且对手剩牌较多(>6)
    if (not is_leader) and bombs and max_opp_cnt > 6 and optimization_turns == 2:
        trigger_strategies.append(TACTICS_DB["situational"]["follow_slow_play_bomb_fallback_2_turns"])
    
    if (not is_leader) and has_red_heart_2 and can_play_moves:
        bomb_moves = [m for m in (can_play_moves or []) if (m.get('type') or 0) >= 20]
        wild_normal_moves = [
            m for m in (can_play_moves or [])
            if (m.get('type') or 0) < 20 and move_uses_red_heart_2(m)
        ]
        
        # [精准策略] 1v1残局+两轮出尽：要在手牌有红桃2可以配大牌或炸弹下采用，没有红桃2时仍沿用原有策略。
        if active_opponents_count == 1 and (optimization_turns == 2) and last_move:
            # 标记是否可以用红桃2凑成强势跟牌 (炸弹或点数>=15的大牌型)
            rh2_power_moves = [
                m for m in (can_play_moves or [])
                if move_uses_red_heart_2(m) and ((m.get('type') or 0) >= 20 or normalize_rank_value(m.get('rank')) >= 15)
            ]
            
            if rh2_power_moves:
                lm_type = last_move.get('type')
                # 针对对手出三张/三带二，若能配成炸弹直接压死
                if lm_type in (3, 4) and any(m.get('type', 0) >= 20 for m in rh2_power_moves):
                    trigger_strategies.insert(0, TACTICS_DB["situational"]["rh2_bomb_force_1v1_finish"])

                # 针对其他牌型，若能配成如“对2”、“三张2”等大牌型接权
                elif any(normalize_rank_value(m.get('rank')) >= 15 for m in rh2_power_moves):
                    trigger_strategies.append(TACTICS_DB["situational"]["rh2_big_combo_1v1_takeover"])

        if bomb_moves and wild_normal_moves:
            trigger_strategies.append(TACTICS_DB["situational"]["reserve_wild_for_bomb_follow"])

    # 跟牌时：若同时存在“含红桃2炸弹”和“非红桃2炸弹”，优先非红桃2炸弹
    if has_red_heart_2 and last_move and can_play_moves:
        bomb_moves = [m for m in (can_play_moves or []) if (m.get('type') or 0) >= 20]
        wild_bombs = [m for m in bomb_moves if move_uses_red_heart_2(m)]
        non_wild_bombs = [m for m in bomb_moves if not move_uses_red_heart_2(m)]
        if wild_bombs and non_wild_bombs and last_move.get('player') in (opponents or []):
            title, base_desc = TACTICS_DB["situational"]["reserve_wild_for_bomb_follow"]
            extra = ""
            preview_non = describe_bomb_moves(non_wild_bombs)
            preview_wild = describe_bomb_moves(wild_bombs)
            if preview_non or preview_wild:
                extra = " 可用替代："
                if preview_non:
                    extra += f"非红桃2炸弹{preview_non}"
                if preview_wild:
                    extra += f"；红桃2炸弹{preview_wild}"
            trigger_strategies.append((title, base_desc + extra))

    # 避免用红桃2去补普通组合（顺子/连对/钢板/三带二）：若存在不耗红桃2的替代方案则优先使用
    # 触发条件：(首发 or 跟牌) + 有红桃2 + 存在"用红桃2补的普通组合" + 存在"不用红桃2的替代"
    normal_combo_types = {4, 5, 6, 7}  # 三带二/顺子/连对/钢板
    if has_red_heart_2 and can_play_moves:
        combos_with_wild = [
            m for m in (can_play_moves or [])
            if (m.get('type') in normal_combo_types) and move_uses_red_heart_2(m)
        ]

        if combos_with_wild:
            # 找"不用红桃2"的同类替代（按 type 分组）
            has_alternative = False
            alt_details = []

            by_type = {}
            for m in combos_with_wild:
                mt = m.get('type')
                if mt not in by_type:
                    by_type[mt] = []
                by_type[mt].append(m)

            for mt, wild_list in by_type.items():
                non_wild_same_type = [
                    m for m in (can_play_moves or [])
                    if (m.get('type') == mt) and (not move_uses_red_heart_2(m))
                ]

                if non_wild_same_type:
                    has_alternative = True
                    type_name = {4: "三带二", 5: "顺子", 6: "连对", 7: "钢板"}.get(mt, f"type={mt}")

                    if mt == 6:
                        alt_details.append(f"不耗红桃2的{type_name}" + describe_pad_moves(non_wild_same_type, "consecutive_pairs"))
                    elif mt == 7:
                        alt_details.append(f"不耗红桃2的{type_name}" + describe_plate_moves(non_wild_same_type))
                    elif mt == 4:
                        alt_details.append(f"不耗红桃2的{type_name}" + describe_pad_moves(non_wild_same_type, "triple_pair"))
                    else:
                        alt_details.append(f"不耗红桃2的{type_name}" + describe_pad_moves(non_wild_same_type, "straight"))

            if has_alternative:
                title, base_desc = TACTICS_DB["situational"]["reserve_wild_for_bomb_follow"]
                extra = (" 可选替代：" + "；".join(alt_details)) if alt_details else ""
                trigger_strategies.append((title, base_desc + extra))
                avoid_wild_hint_added = True

    # 若已提示“红桃2不要用来补普通组合”，则不再重复输出基础红桃2提醒
    if avoid_wild_hint_added:
        base_title, _ = TACTICS_DB["specials"]["red_heart_2"][0]
        trigger_strategies = [
            s for s in trigger_strategies
            if not (isinstance(s, (tuple, list)) and len(s) >= 1 and s[0] == base_title)
        ]

    # 2.4 残局针对性战术
    if remaining_counts:
        has_low_card_opponent = False
        for opp in opponents:
            count = remaining_counts.get(get_p_name(opp), 99)
            if 1 <= count <= 6:
                has_low_card_opponent = True
                trigger_strategies.append((f"警报：对手 {opp} 仅剩 {count} 张牌！", ""))

                advice = TACTICS_DB["end_game_counts"].get(count)
                if advice:
                    if game_stage == "残局阶段" and count == 2:
                        trigger_strategies.append((f"【最高优先】{advice[0]}", advice[1]))
                    else:
                        trigger_strategies.append(advice)

        if has_low_card_opponent:
            trigger_strategies.append(TACTICS_DB["end_game_counts"]["general"])

        # 对手仅剩1张：优先选择打完后“更少孤张/更少小孤张”的出牌，减少下一轮被迫首发单张放走对手的概率
        try:
            opponent_has_1 = any(int(remaining_counts.get(get_p_name(opp), 99)) == 1 for opp in opponents)
        except Exception:
            opponent_has_1 = False

        if opponent_has_1 and hand_cards and can_play_moves:
            # 当对手报单且我方推演剩2/3/4轮时：区分 1v1 / 2v1 / 2v2 的处理
            active_opponents = []
            teammate_finished = False
            teammate_still_playing = False
            mode_note = ""
            extra_detail = ""

            try:
                active_opponents = [
                    o for o in (opponents or [])
                    if (get_p_name(o) not in (finished_players or [])) and int(remaining_counts.get(get_p_name(o), 0)) > 0
                ]
            except Exception:
                active_opponents = []

            teammate_finished = get_p_name(teammate) in (finished_players or [])
            teammate_still_playing = bool(teammate) and (get_p_name(teammate) not in (finished_players or [])) and int(remaining_counts.get(get_p_name(teammate), 0)) > 0

            # 评估手中“卫士”与“碎牌”
            # 卫士 (Guard): Rank >= 15 (2, 小王, 大王) 或 炸弹
            # 碎牌 (Splinter): Rank <= 12 的孤张 (或者容易被迫变成单张的小牌)
            def is_guard_move(m):
                if (m.get('type') or 0) >= 20: return True
                rk = normalize_rank_value(m.get('rank'))
                if rk is not None and rk >= 15: return True
                return False

            hand_counts = {}
            for cid in (hand_cards or []):
                rv = parse_rank_from_card_id(cid)
                if rv is not None:
                    hand_counts[rv] = hand_counts.get(rv, 0) + 1
            small_singles = [r for r, c in hand_counts.items() if c == 1 and r <= 12] # k = 12 (Q)
            small_singles_count = len(small_singles)

            # 触发慢打策略条件：1v2或2v2、对手报单、我是首发、碎牌(<=Q)数量>=2、手里有卫士
            # [Optimization] 当推演轮次 >= 3 且碎牌较多时，严禁消耗红桃2去“避开报单对手”
            wild_move_ids = []
            for m in can_play_moves:
                cids = m.get('card_ids') or []
                is_wild = False
                for cid in cids:
                    cid_s = str(cid)
                    if any(tok in cid_s for tok in ['H15', 'H-15', 'H2', '♥2', 'H2-']):
                        is_wild = True
                        break
                if is_wild and (m.get('type') or 0) < 20: 
                    wild_move_ids.append(m.get('id'))
            
            has_guards = any(is_guard_move(m) for m in (can_play_moves or []))

            is_slow_play_triggered = (
                is_leader and 
                len(active_opponents) >= 2 and 
                (small_singles_count >= 2) and
                has_guards and
                (optimization_turns is None or optimization_turns >= 3)
            )

            # 提取文案
            is_team_mode = teammate_still_playing and not teammate_finished
            if teammate_finished:
                if len(active_opponents) >= 2:
                    if is_slow_play_triggered:
                        mode_note = TACTICS_DB["situational"]["endgame_opp1_2v2_keep_second_max"][1]
                    else:
                        mode_note = TACTICS_DB["situational"]["mode_note_1v2_alone"][0]
                else:
                    mode_note = TACTICS_DB["situational"]["endgame_opp1_1v1_force_win"][1]
            elif teammate_still_playing and len(active_opponents) == 1:
                mode_note = TACTICS_DB["situational"]["endgame_opp1_2v1_release_teammate"][1]
            elif is_team_mode and len(active_opponents) >= 2:
                if is_slow_play_triggered:
                    mode_note = TACTICS_DB["situational"]["endgame_opp1_2v2_keep_second_max"][1]
                else:
                    mode_note = TACTICS_DB["situational"]["mode_note_2v2_coop"][0]

            if is_leader and is_slow_play_triggered:
                # 执行“次大牌放路”逻辑：过滤掉所有卫士，在剩下牌型中找最强的
                normal_candidates = [m for m in can_play_moves if not is_guard_move(m) and m.get('type') not in (0, None)]
                
                if normal_candidates:
                    # 如果为了“不放行报单对手”而产生的所有非单张选项都包含红桃2，则额外强调
                    non_single_wild_moves = [m for m in normal_candidates if m.get('type') != 1 and m.get('id') in wild_move_ids]
                    
                    resource_warning = ""
                    if non_single_wild_moves:
                        resource_warning = "\n**关键决策**：当前你仅剩的‘避开单张’手段均需格外消耗唯一的【红桃2】资源。由于你还需 3 轮以上才能出完，这种‘避同’代价过大。**严禁为了张数避同而交出红桃2控制权**。应执行‘次强单张’慢打策略，放行报单者，保留核心资源拦截另一对手。"

                    def node_power_key(m):
                        mt = m.get('type') or 0
                        rk = rank_sort_key(m.get('rank'))
                        ln = len(m.get('card_ids') or [])
                        return (mt, rk, ln)

                    # 按战力排序，优先取单张中的“第二大”
                    normal_singles = [m for m in normal_candidates if m.get('type') == 1]
                    if len(normal_singles) >= 2:
                        sorted_singles = sorted(normal_singles, key=node_power_key, reverse=True)
                        best_normal = sorted_singles[1] 
                        rec_desc = f"第二大单张 {best_normal.get('desc')}"
                    else:
                        sorted_normal = sorted(normal_candidates, key=node_power_key, reverse=True)
                        best_normal = sorted_normal[0]
                        rec_desc = best_normal.get('desc')
                    
                    title = "【最高优先】残局慢打分级：放弃无效避同，保留核心资源"
                    desc = (
                        f"检测到对手报单且局面为 {('2v2' if is_team_mode else '1v2')}。{resource_warning}\n"
                        f"1. **战术性放行**：不要为了‘不放单张’而浪费红桃2去改牌型。接受报单对手获胜的代价。\n"
                        f"2. **执行‘次大单张’**：首发打出 **{rec_desc}**。这能顺走碎牌并保留手中‘2/王/炸弹’的绝对控制力。\n"
                        f"3. **锁定名次**：保留控权拦截另一对手，确保你和队友能锁定后续顺位，稳健获胜。"
                    )
                    # 插入最前面
                    trigger_strategies.insert(0, (title, desc))
                else:
                    # 如果只有卫士能出（极罕见），则退化回原本逻辑
                    pass

            # 如果没有触发慢打或没有生成策略，走兜底逻辑
            # [Optimization] 如果已经有“策略性慢打”的最高优先级提示，则不再强制要求“非单张”
            has_slow_play = any(s[0].startswith("【最高优先】") or s[0].startswith("【策略性慢打】") for s in trigger_strategies if isinstance(s, (tuple, list)))
            
            if not has_slow_play:
                candidates = [m for m in (can_play_moves or []) if m.get('type') not in (0, None)]
                # 首发时，除非是慢打，否则依然优先评估非单张以尝试避同
                if is_leader:
                    non_single = [m for m in candidates if m.get('type') != 1]
                    if non_single:
                        candidates = non_single

                if candidates:
                    ranked = sorted(candidates, key=lambda m: release_risk_tuple(hand_cards, m))
                    best = ranked[:5]

                    lines = []
                    for mv in best[:3]:
                        penalty, total_s, small_s, neg_p, neg_t, remain_cards = release_risk_tuple(hand_cards, mv)
                        pairs_left = -neg_p
                        triples_left = -neg_t
                        desc = mv.get('desc')
                        if not desc:
                            desc = f"type={mv.get('type')} rank={format_rank_value(mv.get('rank'))}"
                        
                        penalty_str = " (拆炸弹!)" if penalty > 0 else ""
                        lines.append(
                            f"- {desc}{penalty_str} → 出完后剩牌{remain_cards}张，孤张{total_s}(小孤张{small_s})，对子{pairs_left}"
                        )

                    title, base_desc = TACTICS_DB["situational"]["one_card_opponent_minimize_release"]
                    detail = "推荐候选（按放走风险从低到高）:\n" + "\n".join(lines)
                    merged_desc = base_desc + ("\n" + mode_note if mode_note else "") + "\n" + detail
                    if extra_detail:
                        merged_desc += extra_detail
                    trigger_strategies.append((title, merged_desc))

        # 对手仅剩2张且我方首发：若手里有两手现成对子，优先出较大对子探测/封锁，再用另一手对子收尾
        try:
            active_opponents_for_2 = [
                o for o in (opponents or [])
                if (get_p_name(o) not in (finished_players or [])) and int(remaining_counts.get(get_p_name(o), 0)) > 0
            ]
            opponent_has_2 = any(int(remaining_counts.get(get_p_name(opp), 99)) == 2 for opp in active_opponents_for_2)
        except Exception:
            active_opponents_for_2 = []
            opponent_has_2 = False

        # [Add] 【策略性慢打】对报双对手的纵深试探 (2v1/2v2)
        if opponent_has_2 and is_leader and (get_p_name(teammate) not in (finished_players or [])) and len(active_opponents_for_2) >= 2:
            normal_singles = [m for m in (can_play_moves or []) if m.get('type') == 1 and (normalize_rank_value(m.get('rank')) or 0) < 15]
            if len(normal_singles) >= 2:
                # 按照 Rank 从小到大排序
                sorted_singles = sorted(normal_singles, key=lambda m: rank_sort_key(m.get('rank')))
                rec_move = sorted_singles[1] # 次小单张
                title, base_desc = TACTICS_DB["situational"]["slow_play_depth_opp_2"]
                trigger_strategies.insert(0, (title, f"{base_desc}\n策略提示：推荐首发次小单张【{rec_move.get('desc')}】。"))

        # 1v1 强制争胜：对手剩2张 + 我方两轮出尽且两单张 -> 先出最大单张
        # 仅在 1v1（场上只剩你和一个对手）时生效，避免与 2v2 的“逼拆对/先小后大/放行队友”冲突。
        prefer_combo_finish_over_pair_probe = False
        if opponent_has_2 and is_leader and hand_cards and can_play_moves and (not avoid_same_count_active):
            # 判断是否 1v1：队友已完牌，且对手侧只剩 1 人仍在场
            try:
                active_opponents = [
                    o for o in (opponents or [])
                    if (o not in (finished_players or [])) and int(remaining_counts.get(o, 0)) > 0
                ]
            except Exception:
                active_opponents = []
            is_1v1 = (teammate in (finished_players or [])) and (len(active_opponents) == 1)

            if is_1v1:
                # 1v1 对手剩<=3张：避免首发同张数
                try:
                    opp_min = min(int(remaining_counts.get(o, 99)) for o in active_opponents)
                except Exception:
                    opp_min = 99
                if opp_min <= 3:
                    trigger_strategies.insert(0, TACTICS_DB["situational"]["endgame_1v1_opp_leq3_avoid_same_count"])

            # “两轮出尽”的判断：优先用推演轮次；若推演不可用则退化为只看手牌数==2。
            two_turn_finish = (optimization_turns == 2) or (len(hand_cards) == 2)

            if is_1v1 and opp_min == 2 and two_turn_finish:
                combo_finish_moves = [
                    m for m in (can_play_moves or [])
                    if (m.get('type') or 0) not in (0, 1, 2)
                    and len(m.get('card_ids') or []) > opp_min
                    and len(m.get('card_ids') or []) < len(hand_cards or [])
                ]

                if combo_finish_moves:
                    prefer_combo_finish_over_pair_probe = True
                    combo_finish_moves = sorted(
                        combo_finish_moves,
                        key=lambda m: (
                            0 if (m.get('type') or 0) in (6, 5, 4, 7, 3) else 1,
                            -len(m.get('card_ids') or []),
                            rank_sort_key(m.get('rank'))
                        )
                    )
                    trigger_strategies.insert(
                        0,
                        (
                            TACTICS_DB["situational"]["one_vs_one_two_cards_combo_finish"][0],
                            TACTICS_DB["situational"]["one_vs_one_two_cards_combo_finish"][1]
                            + " 推荐优先考虑："
                            + ", ".join(_move_label(m) for m in combo_finish_moves[:5])
                        )
                    )


        if opponent_has_2 and is_leader and pairs_rank_values and len(pairs_rank_values) >= 2 and can_play_moves and (not prefer_combo_finish_over_pair_probe):
            pair_moves = [
                m for m in (can_play_moves or [])
                if m.get('type') == 2
            ]
            natural_pair_moves = filter_by_structure(pair_moves, pairs_rank_values)
            if natural_pair_moves:
                trigger_strategies.append(
                    build_pad_entry(
                        "two_card_opponent_pair_probe_with_two_pairs",
                        natural_pair_moves,
                        "pair"
                    )
                )

    # 2.5 跟牌时：若手里存在炸弹且当前跟牌候选中出现“拆炸弹拼普通牌型”，强提醒优先直接用炸弹
    if last_move and hand_cards and can_play_moves:
        try:
            lm_player = last_move.get('player')
            lm_type = int(last_move.get('type') or 0)
        except Exception:
            lm_player = last_move.get('player')
            lm_type = last_move.get('type') or 0

        # 仅在对手出普通牌型（非炸弹）且轮到我跟牌时提示
        if lm_player in (opponents or []) and lm_type and lm_type < 20:
            # 我方可出的炸弹候选
            bomb_moves = [m for m in (can_play_moves or []) if (m.get('type') or 0) >= 20]
            if bomb_moves:
                # 从手牌统计自然炸弹点数（>=4 张同点数）
                hand_rank_counts: dict[int, int] = {}
                for cid in (hand_cards or []):
                    rv = parse_rank_from_card_id(cid)
                    if rv is None:
                        continue
                    hand_rank_counts[rv] = hand_rank_counts.get(rv, 0) + 1

                natural_bomb_ranks = {r for r, c in hand_rank_counts.items() if c >= 4}
                if natural_bomb_ranks:
                    # 找到那些“普通牌型”但用到了炸弹点数的一部分牌（典型：5张J炸 -> JJJQQ）
                    split_bomb_normal_moves = []
                    for mv in (can_play_moves or []):
                        mt = mv.get('type') or 0
                        if mt <= 0 or mt >= 20:
                            continue
                        counts_in_move: dict[int, int] = {}
                        for cid in (mv.get('card_ids') or []):
                            rv = parse_rank_from_card_id(cid)
                            if rv is None:
                                continue
                            counts_in_move[rv] = counts_in_move.get(rv, 0) + 1

                        is_split = False
                        for br in natural_bomb_ranks:
                            used = counts_in_move.get(br, 0)
                            have = hand_rank_counts.get(br, 0)
                            if 0 < used < have:
                                is_split = True
                                break
                        if is_split:
                            split_bomb_normal_moves.append(mv)

                    if split_bomb_normal_moves:
                        # 例外：若“拆炸弹拼普通牌型”能直接出完（斩杀），则允许作为例外选项
                        kill_split_moves = [
                            mv for mv in split_bomb_normal_moves
                            if (len(hand_cards or []) - len(mv.get('card_ids') or [])) <= 0
                        ]
                        non_kill_split_moves = [
                            mv for mv in split_bomb_normal_moves
                            if mv not in kill_split_moves
                        ]

                        if non_kill_split_moves:
                            # 最高优先：合并炸弹提示，避免重复轰炸提示
                            bomb_title, bomb_desc = TACTICS_DB["situational"]["bomb_no_split_follow_any"]
                            avoid_desc = "应避免候选：" + describe_split_bomb_normal_moves(non_kill_split_moves)
                            bomb_alts = describe_bomb_moves(bomb_moves)
                            if bomb_alts:
                                bomb_alts = "可用炸弹候选：" + bomb_alts
                            merged_desc = f"{bomb_desc} {avoid_desc}"
                            if bomb_alts:
                                merged_desc += f" {bomb_alts}"

                            trigger_strategies[0:0] = [
                                ("【关键】拆炸弹跟牌警告", merged_desc),
                            ]

                        if kill_split_moves:
                            trigger_strategies.append((
                                "拆炸弹跟牌例外（可直接斩杀）",
                                "仅当该拆法能让你**当回合直接出完/立刻获胜**时，才允许拆炸弹去拼普通牌型。"
                                f"{describe_pad_moves(kill_split_moves, 'triple_pair')}"
                            ))

            # 2.6 额外检测：用了多张红桃2的普通组合（连对/顺子/钢板等）
            # 若某个普通组合用了2张及以上红桃2，也应被列入"应避免候选"（尤其在有炸弹/大牌可控时）
            multi_wild_normal = []
            for mv in (can_play_moves or []):
                mt = mv.get('type') or 0
                if mt <= 0 or mt >= 20:
                    continue
                wild_count = 0
                for cid in (mv.get('card_ids') or []):
                    s = str(cid).upper()
                    if 'H15' in s or 'H-15' in s or '♥2' in s:
                        wild_count += 1
                if wild_count >= 2:
                    multi_wild_normal.append(mv)

            if multi_wild_normal:
                # 插入到最高优先级（紧随拆炸弹警告之后，或若无拆炸弹则在最前）
                insert_pos = 0
                # 找到拆炸弹相关提示的结束位置
                for idx, (t, _) in enumerate(trigger_strategies):
                    if "拆炸弹" in t or "炸弹候选" in t:
                        insert_pos = idx + 1
                trigger_strategies.insert(
                    insert_pos,
                    (
                        "【高优先】应避免：用多张红桃2补普通牌型",
                        "以下候选会消耗多张红桃2（珍贵资源），通常应避免。红桃2应优先留给升级大炸/关键断牌/斩杀，而不是用来补连对/顺子等普通组合："
                        + describe_split_bomb_normal_moves(multi_wild_normal)
                    )
                )

    if flow_strategies:
        if suppress_pass_strategy:
            pass_title = TACTICS_DB["situational"]["pass_strategy"][0]
            flow_strategies = [s for s in flow_strategies if not (isinstance(s, (tuple, list)) and len(s) >= 1 and s[0] == pass_title)]
        trigger_strategies = flow_strategies + trigger_strategies

    # === 仅对“牌局触发提醒”做去重 + 分级裁剪（配置化） ===
    cfg = TACTICS_DB.get("strategy_output_config", {})
    max_items_cfg = cfg.get("trigger_max_items", 8)
    if isinstance(max_items_cfg, dict):
        stage_key_map = {
            "开局阶段": "opening",
            "中局阶段": "mid_game",
            "残局阶段": "end_game"
        }
        stage_key = stage_key_map.get(game_stage)
        max_items = int(max_items_cfg.get(stage_key, max_items_cfg.get("opening", 8)))
    else:
        max_items = int(max_items_cfg)
    priority_prefixes = cfg.get("priority_prefixes", {})
    highest_prefixes = tuple(priority_prefixes.get("highest", ["【最高优先】", "警报："]))
    high_prefixes = tuple(priority_prefixes.get("high", ["【关键】", "【高优先】"]))

    def _priority_of(title: str) -> int:
        if not title:
            return 1
        if title.startswith(highest_prefixes):
            return 3
        if title.startswith(high_prefixes):
            return 2
        return 1

    def _dedupe_and_trim(strategies: list, max_items: int) -> list:
        if not strategies:
            return strategies

        # --- [Add] 战术冲突清洗 (Conflict Resolution) ---
        # TACTICS_DB 条目为 JSON 数组(list)，内联条目为 tuple；两者都视为 (标题, 内容)
        def _entry_title(s):
            if isinstance(s, (tuple, list)) and len(s) >= 1 and isinstance(s[0], str):
                return s[0]
            return ""

        refined_strategies = []
        has_opp1 = any("对手剩1张" in _entry_title(s) for s in strategies)
        has_slow_play_top = any("慢打控权策略" in _entry_title(s) for s in strategies)
        has_combo_finish_top = any("现成组合两轮直胜" in _entry_title(s) for s in strategies)
        has_plate_preserve_takeover_top = any("钢板顺出后续接管" in _entry_title(s) for s in strategies)

        for item in strategies:
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            title, content = item
            
            # 冲突规避 A：有了专门的“报单对手拦截/慢打”，移除通用的“残局最后三轮首发”提醒
            if has_opp1 and "残局最后三轮·首发提醒" in title:
                continue
            # 冲突规避 B：有了具体的“慢打”决策，移除重复的“碎牌慢打”或“针对4张牌”等普适策略
            if has_slow_play_top and ("多孤张试探" in title or "针对 4 张牌" in title):
                continue
            # 冲突规避 C：有了“现成组合两轮直胜”，移除会把模型带回对子路线的策略
            if has_combo_finish_top and ("两手对子优先出对子探测/封锁" in title or title == "对手剩2张牌"):
                continue
            # 冲突规避 D：有了“钢板顺出后续接管”，移除与之冲突的“先炸”提示
            if has_plate_preserve_takeover_top and ("拆炸弹跟牌警告" in title or "跟牌时禁止拆炸弹拼普通牌型" in title):
                continue
            
            refined_strategies.append(item)
        
        strategies = refined_strategies
        seen = set()
        result = []

        def take_with_min_priority(min_pr: int) -> bool:
            nonlocal result, seen
            for item in strategies:
                if not isinstance(item, (tuple, list)) or len(item) < 2:
                    continue
                title = item[0]
                if title in seen:
                    continue
                pr = _priority_of(title)
                if pr >= min_pr:
                    seen.add(title)
                    result.append(item)
                    if len(result) >= max_items:
                        return True
            return False

        for level in (3, 2, 1):
            if take_with_min_priority(level):
                break

        # === 修复：确保【最高优先】及【关键/高优先】策略不受数量限制 ===
        # 如果列表中包含高优先级策略被截断了，需要强制找回
        # 匹配 prefixes: "【最高优先】", "警报：", "【关键】", "【高优先】"
        critical_markers = ["【最高优先】", "警报：", "【关键】", "【高优先】"]
        
        high_prio_strategies = [
            item for item in strategies 
            if any(marker in item[0] for marker in critical_markers)
        ]
        
        # 将 result 中没有的高优先级策略补回来
        current_titles = {item[0] for item in result}
        for hp_item in high_prio_strategies:
            if hp_item[0] not in current_titles:
                result.insert(0, hp_item)
        
        # === 策略抑制逻辑：配合队友完牌优先 ===
        # 如果存在“配合队友完牌”的最高优先级策略，则移除可能与之冲突的“慢打”或“封锁”策略
        has_teammate_coop = any("配合队友完牌" in item[0] for item in result)
        if has_teammate_coop:
            filtered_result = []
            for item in result:
                title = item[0]
                # 如果是配合队友策略本身，保留
                if "配合队友完牌" in title:
                    filtered_result.append(item)
                    continue
                # 抑制冲突策略：慢打、针对对手的封锁、或者干扰放行的“第二大牌”提示
                suppress_keywords = ["慢打", "封锁方案", "第二大牌", "避张风险", "Minimize Release"]
                if any(kw in title for kw in suppress_keywords):
                    continue
                filtered_result.append(item)
            result = filtered_result

        return result

    trigger_strategies = _dedupe_and_trim(trigger_strategies, max_items)

    return stage_info, stage_strategies, trigger_strategies, optimization_data
