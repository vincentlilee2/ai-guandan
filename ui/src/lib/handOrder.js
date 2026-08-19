// 手牌本地排列（v3.2）：服务器 myHand 仍是出牌权威数据源，
// 这里只做"用户手动整理后的展示顺序"与服务器手牌集合的合并。
// 纯函数，便于单测。

// 合并用户排列与服务器手牌：
// 1) 保留 userOrder 中仍在 serverHand 里的卡（维持相对顺序）；
// 2) 新出现的卡（重发/补牌）按服务器顺序追加到尾部；
// 3) 手牌被整体替换时，filter-then-append 自然退化为服务器顺序（即重置）。
export function reconcileHandOrder(userOrder, serverHand) {
  if (!Array.isArray(serverHand)) return Array.isArray(userOrder) ? userOrder : [];
  if (!Array.isArray(userOrder) || userOrder.length === 0) {
    return serverHand.slice();
  }
  const serverIds = new Set(serverHand);
  const kept = userOrder.filter(id => serverIds.has(id));
  const keptIds = new Set(kept);
  const added = serverHand.filter(id => !keptIds.has(id));
  return [...kept, ...added];
}

// 对扁平顺序去重并生成稳定唯一 key（React key 用）。
// 服务器同一手牌可能出现重复 id，展示 key 必须唯一。
export function cardsWithKeys(flatOrder) {
  const seen = new Set();
  const out = [];
  for (const id of flatOrder) {
    if (seen.has(id)) continue;
    seen.add(id);
    out.push({ id, key: `${id}-${seen.size - 1}` });
  }
  return out;
}

// 两行布局：row0 = 前 half 张，row1 = 后半。half 与渲染拆分保持一致。
export function computeRowSplit(count) {
  return Math.ceil(count / 2);
}

// (row, slot) → 扁平插入索引；half = computeRowSplit(总卡数)。
export function rowSlotToFlatIndex(row, slot, half) {
  return row === 0 ? slot : half + slot;
}
