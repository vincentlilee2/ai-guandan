# backend/scoring.py
from typing import List, Dict

class ScoreManager:
    def __init__(self):
        # 记录本局所有触发翻倍的炸弹 (存储张数，999代表天王炸)
        self.bomb_history: List[int] = []
        self.MAX_SCORE = 2400
        
        # 队伍定义
        self.teams = {
            "User": "TeamA", "PartnerBot": "TeamA",
            "RightBot": "TeamB", "LeftBot": "TeamB"
        }

    def to_dict(self):
        """序列化为可 JSON 存储的 dict（Redis 持久化用）。"""
        return {
            "bomb_history": list(self.bomb_history),
            "MAX_SCORE": self.MAX_SCORE,
            "teams": dict(self.teams),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScoreManager":
        sm = cls()
        if not d:
            return sm
        if isinstance(d.get("bomb_history"), list):
            sm.bomb_history = list(d["bomb_history"])
        if "MAX_SCORE" in d:
            sm.MAX_SCORE = int(d["MAX_SCORE"])
        if isinstance(d.get("teams"), dict):
            sm.teams = dict(d["teams"])
        return sm

    def record_bomb(self, cards, card_type: int):
        """
        每次出牌调用。如果是6+炸弹或天王炸，记录下来。
        cards: List[Card]
        card_type: int (from rules.CardType)
        """
        # 只有炸弹类型才可能翻倍 (CardType.BOMB_4 = 20)
        # 连对(6)、钢板(7) 等虽然张数可能>=6，但不计分
        if card_type < 20:
            return

        n = len(cards)
        # 检查四大天王 (Type 100)
        if card_type == 100:
            self.bomb_history.append(999)
            return

        # 检查6张及以上炸弹
        if n >= 6:
            self.bomb_history.append(n)

    def calculate_multiplier(self):
        """
        计算累计倍数
        规则：6张x2, 7张x4, 8张/天王x8... 累积相乘
        返回: (total_mult, details_list)
        """
        total_mult = 1
        details = []
        
        for b in self.bomb_history:
            if b == 999: # 天王炸
                mult = 8
                total_mult *= mult
                details.append("天王炸 x8")
            else:
                # 6->2, 7->4, 8->8
                exponent = b - 5
                mult = pow(2, exponent)
                total_mult *= mult
                details.append(f"{b}张炸弹 x{mult}")
                
        return total_mult, details

    def calculate_final_score(self, finish_order: List[str]):
        """
        结算分数
        finish_order: ["User", "RightBot", "PartnerBot", "LeftBot"]
        """
        if len(finish_order) != 4: return None
        
        first, second, third, fourth = finish_order
        team1 = self.teams[first]
        team2 = self.teams[second]
        team3 = self.teams[third]
        
        base_score = 0
        winners = []
        losers = []
        
        # 双游 (1,2 一队)
        if team1 == team2:
            base_score = 300
            winners = [first, second]
            losers = [third, fourth]
        # 一三游 (1,3 一队)
        elif team1 == team3:
            base_score = 200
            winners = [first, third]
            losers = [second, fourth]
        # 单游 (1,4 一队)
        else:
            base_score = 100
            winners = [first, fourth]
            losers = [second, third]
            
        mult, mult_details = self.calculate_multiplier()
        raw_score = base_score * mult
        final_score = min(raw_score, self.MAX_SCORE)
        
        results = {}
        for p in winners: results[p] = final_score
        for p in losers: results[p] = -final_score
        
        return {
            "scores": results,
            "rank_order": finish_order, 
            "info": {
                "type": "双游" if base_score==300 else ("一三游" if base_score==200 else "单游"),
                "base": base_score,
                "mult": mult,
                "mult_details": mult_details,
                "capped": raw_score > self.MAX_SCORE
            }
        }# Scoring logic for the game backend