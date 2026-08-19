import os
import json
import re
import logging
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 尝试初始化 Coach 客户端
try:
    coach_client = None
    # 优先使用 COACH_OPENAI_API_KEY，如果没有则使用 OPENAI_API_KEY
    api_key = os.getenv("COACH_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    
    if api_key and api_key.startswith("sk-"):
        coach_client = OpenAI(
            api_key=api_key,
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        )
except:
    coach_client = None

ADVICE_FILE = "coach_advice.json"

def analyze_game(history_file="game_history.json"):
    """
    分析游戏历史，生成建议并保存
    """
    if not coach_client:
        logger.warning("Coach AI 客户端未初始化，跳过分析")
        return

    if not os.path.exists(history_file):
        logger.warning(f"找不到游戏历史文件: {history_file}")
        return

    try:
        with open(history_file, 'r', encoding='utf-8') as f:
            game_data = json.load(f)
            
        history = game_data.get("history", [])
        initial_hands = game_data.get("initial_hands", {})
        winner_order = game_data.get("winner_order", [])
        players = game_data.get("players", [])
        
        if not history:
            logger.warning("游戏历史为空")
            return

        # 构造 Prompt
        history_str = json.dumps(history, indent=2, ensure_ascii=False)
        hands_str = json.dumps(initial_hands, indent=2, ensure_ascii=False)
        
        prompt = f"""
        你是掼蛋顶级教练。请基于全局视角（记住了该玩家手牌、本轮出牌情况及之前各轮其他玩家出牌记录）分析以下一局掼蛋游戏的完整数据。
        
        【游戏数据】
        1. **玩家列表**: {players}
        2. **队伍分配**: 
           - 队伍A: User 和 PartnerBot
           - 队伍B: RightBot 和 LeftBot
        3. **完牌顺序**: {winner_order}
        4. **初始手牌**: 
        {hands_str}
        5. **出牌日志**:
        {history_str}

        【分析要求】
        请站在**全局信息**的高度，结合玩家当时的**手牌状态**、**剩余手牌**以及**队友/对手的牌型及之前各轮出牌记录**，对 AI 玩家 (RightBot, PartnerBot, LeftBot, User) 的表现进行深度复盘。
        
        重点关注以下维度：
        1. **全局大局观**：
           - 玩家是否在明知队友能走的情况下，错误地抢牌或阻挡？
           - 玩家是否在明知对手要走的情况下，保留了关键阻截牌没出？
           - 结合初始手牌，判断某次出牌是否导致了后续手牌结构的崩坏（如拆了炸弹却没能走掉）。
        
        2. **团队配合策略 (Team Strategy)**：
           - **核心目标**：最大化本队得分（双游 > 一三游 > 单游）。
           - **牺牲精神**：当队友牌力极强时，是否懂得PASS让路？当队友需要过牌时，是否送了合适的牌型？
           - **掩护配合**：是否在自己无望走完时，全力消耗对手的大牌，为队友创造机会？
        
        3. **具体战术失误**：
           - **消极怠工**：在该出牌的时候选择 PASS (特别是接风、或者对手出小牌时)。
           - **浪费资源**：不合理地使用炸弹或红桃2 (逢人配)。
           - **错失良机**：有机会走完或接管比赛却没做。

        请输出一个 JSON 列表，包含你对 AI 玩家的指导意见。
        每个建议对象应包含：
        - "player": 玩家名称 (RightBot, PartnerBot, LeftBot)
        - "situation": 简述当时的情况 (如 "第15手，对手出对3，你手上有对5和对A")
        - "mistake": 犯了什么错 (结合全局视角，例如 "此时队友手上有炸弹，你应该逼出对手的炸弹")
        - "advice": 指导意见 (简短、原则性，用于加入 Prompt 自我学习)

        示例格式:
        [
            {{
                "player": "PartnerBot",
                "situation": "第20手，User出单张3，RightBot出单张K",
                "mistake": "你选择了PASS，但你手上有单张A和2。虽然K很大，但考虑到User是你队友且只剩3张牌，你应该顶住RightBot，或者如果User是想过牌，你应该根据User的手牌结构判断。",
                "advice": "当队友处于冲刺阶段，必须全力阻击对手的拦截，或者送出队友需要的牌型。"
            }}
        ]
        """

        response = coach_client.chat.completions.create(
            model=os.getenv("COACH_MODEL_NAME", "gpt-5.1"), # 教练最好用更强的模型
            messages=[
                {"role": "system", "content": "你是掼蛋战术大师，负责指导 AI 玩家提高水平。请直接输出 JSON 格式，不要包含 Markdown 代码块。"},
                {"role": "user", "content": prompt}
            ],
            # response_format={"type": "json_object"}, # 某些模型不支持此参数，暂时移除
            # temperature=0.2
        )
        
        content = response.choices[0].message.content.strip()
        
        # 增强的 JSON 提取逻辑
        # 1. 尝试匹配 Markdown 代码块
        json_match = re.search(r'```(?:json)?\s*(.*?)```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1).strip()
        else:
            # 2. 如果没有代码块，尝试寻找最外层的 [] 或 {}
            # 优先找列表 []
            start_list = content.find('[')
            end_list = content.rfind(']')
            
            start_dict = content.find('{')
            end_dict = content.rfind('}')
            
            if start_list != -1 and end_list != -1 and end_list > start_list:
                # 简单的判断：如果列表范围比字典范围大，或者没有字典，就用列表
                if start_dict == -1 or (start_list < start_dict and end_list > end_dict):
                    content = content[start_list:end_list+1]
                elif start_dict != -1:
                    content = content[start_dict:end_dict+1]
            elif start_dict != -1 and end_dict != -1 and end_dict > start_dict:
                content = content[start_dict:end_dict+1]

        # 使用 strict=False 允许控制字符（如换行符）在字符串中
        try:
            result = json.loads(content, strict=False)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析失败: {e}. Content snippet: {content[:100]}...")
            return

        new_advice = []
        if isinstance(result, list):
            new_advice = result
        elif isinstance(result, dict):
            new_advice = result.get("advice_list", result.get("advice", []))
        
        # 保存建议
        save_advice(new_advice)
        logger.info(f"Coach 分析完成，生成了 {len(new_advice)} 条建议")
        
    except Exception as e:
        logger.error(f"Coach 分析失败: {e}")

def save_advice(new_advice):
    """
    保存新建议到 advice 文件中 (覆盖旧建议)
    """
    # 过滤掉非 AI 的建议 (User)
    ai_players = ["RightBot", "PartnerBot", "LeftBot", "User"]
    valid_advice = [a for a in new_advice if a.get("player") in ai_players]
    
    # 直接覆盖写入，不保留历史建议
    with open(ADVICE_FILE, 'w', encoding='utf-8') as f:
        json.dump(valid_advice, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    analyze_game()
