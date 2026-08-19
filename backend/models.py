# Models for the game backend
# backend/models.py
from enum import Enum, IntEnum
from dataclasses import dataclass, field
from typing import List, Optional

class Suit(str, Enum):
    HEARTS = 'H'   # 红桃
    DIAMONDS = 'D' # 方片
    CLUBS = 'C'    # 梅花
    SPADES = 'S'   # 黑桃
    JOKER = 'J'    # 王

class Rank(IntEnum):
    # 数值越小牌越小，用于排序和比大小
    R3 = 3
    R4 = 4
    R5 = 5
    R6 = 6
    R7 = 7
    R8 = 8
    R9 = 9
    R10 = 10
    RJ = 11
    RQ = 12
    RK = 13
    RA = 14
    R2 = 15       # 级牌2，权重高于A
    R_SMALL = 20  # 小王
    R_BIG = 21    # 大王

@dataclass
class Card:
    suit: Suit
    rank: Rank
    id: str = field(default="") # 用于前端唯一标识

    def __post_init__(self):
        # 兼容传入 Suit 枚举或字符串
        if isinstance(self.suit, str):
            self.suit = Suit(self.suit)
        if isinstance(self.rank, int):
            self.rank = Rank(self.rank)
            
        # 如果没传ID，生成简易ID
        # 注意：实际发牌时应覆盖此ID以确保两副牌不冲突
        if not self.id:
            self.id = f"{self.suit.value}-{self.rank.value}"

    @property
    def is_wild(self) -> bool:
        """是否为逢人配（红桃2）"""
        return self.rank == Rank.R2 and self.suit == Suit.HEARTS

    def __repr__(self):
        # 方便调试打印
        suit_symbol = {
            'H': '♥', 'D': '♦', 'C': '♣', 'S': '♠', 'J': 'JK'
        }
        r_str = str(self.rank.value)
        if self.rank == Rank.RJ: r_str = 'J'
        elif self.rank == Rank.RQ: r_str = 'Q'
        elif self.rank == Rank.RK: r_str = 'K'
        elif self.rank == Rank.RA: r_str = 'A'
        elif self.rank == Rank.R2: r_str = '2'
        elif self.rank == Rank.R_SMALL: r_str = '小王'
        elif self.rank == Rank.R_BIG: r_str = '大王'
        
        return f"{suit_symbol.get(self.suit.value, '?')}{r_str}"

    # 用于排序
    def __lt__(self, other):
        if self.rank != other.rank:
            return self.rank < other.rank
        return self.suit < other.suit