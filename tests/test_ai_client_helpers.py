"""ai_client 新增的防幻觉/防浪费匹配辅助函数测试。

回归两个真实对局问题：
1. LLM 输出 JSON 无法严格解析（如 thought 含裸换行），但正则已提取出有效决策 → 应当直接采用，不再浪费一次请求重试（Fix A）。
2. LLM 幻觉：选了 ID 但 desc 与真实牌型不符 → 应从提示词纠错中恢复（Fix B 的 desc 对照喂回）。

覆盖 _type_keyword / _real_type_name / _desc_matches_move 三种匹配逻辑。
"""
import pytest

from backend.ai_client import (
    _type_keyword,
    _real_type_name,
    _desc_matches_move,
)


class TestTypeKeyword:
    def test_pass_variants(self):
        assert _type_keyword("我选择放弃") == "PASS"
        assert _type_keyword("不PASS") == "PASS"

    def test_specific_type_before_generic(self):
        """三带二要先于“三张”匹配；单张不能被“三”误伤。"""
        assert _type_keyword("三带二 888带33") == "三带二"
        assert _type_keyword("对子 99") == "对子"
        assert _type_keyword("单张 5") == "单张"

    def test_unknown(self):
        assert _type_keyword("随便出张牌") == "未知"


class TestRealTypeName:
    def test_mapping(self):
        assert _real_type_name(0) == "PASS"
        assert _real_type_name(1) == "单张"
        assert _real_type_name(2) == "对子"
        assert _real_type_name(4) == "三带二"
        assert _real_type_name(20) == "炸弹"
        assert _real_type_name(30) == "炸弹"  # 同花顺是顶级炸弹
        assert _real_type_name(-1) == "未知"


class TestDescMatchesMove:
    def test_exact_type(self):
        assert _desc_matches_move("对子 99", {"id": 0, "type": 2, "desc": "对子 99"})

    def test_real_type_appears_in_desc(self):
        """模型描述是“钢板(非炸弹)”时，真实类型“钢板”出现在描述里 → 通过。"""
        assert _desc_matches_move("钢板 555666", {"id": 0, "type": 7, "desc": "钢板 555666"})

    def test_choice_desc_contains_real_desc(self):
        """AI 在原样复制的 desc 后附加说明 → 通过。"""
        assert _desc_matches_move("顺子 3-7 (过桥)", {"id": 1, "type": 5, "desc": "顺子 3-7"})

    def test_hallucination_mismatch(self):
        """幻觉：说“单张 3”却选了炸弹 → 不通过，触发纠错重试。"""
        assert not _desc_matches_move("单张 3", {"id": 2, "type": 20, "desc": "炸弹 8888"})

    def test_unknown_desc_trusts_id(self):
        """描述无法识别类型时默认信任 ID（防过度严格）。"""
        assert _desc_matches_move("随便出", {"id": 3, "type": 1, "desc": "单张 3"})

    def test_pass_move(self):
        assert _desc_matches_move("不出", {"id": 0, "type": 0, "desc": "PASS"})
