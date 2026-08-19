# 语音播报顺序混乱问题修复

## 问题分析

### 原有问题
1. **无队列管理**：每次调用 `playAiVoice` 都直接创建新的 Audio 对象并播放
2. **并发播放冲突**：多个 AI 快速出牌时，语音会同时播放导致重叠混乱
3. **网络延迟不可控**：mp3 文件加载时间不确定，导致播放顺序错乱
4. **系统默认出牌**：后端自动出牌时，前端可能同时收到多条历史记录

## 解决方案

### 核心改动
引入**语音播放队列系统**，确保语音按顺序依次播放：

#### 1. 新增状态管理（App.jsx 第92-95行）
```javascript
const voiceQueueRef = useRef([]);           // 待播放语音队列
const isPlayingVoiceRef = useRef(false);    // 是否正在播放语音
const currentAudioRef = useRef(null);       // 当前正在播放的Audio对象
```

#### 2. 队列处理机制（新增 `processVoiceQueue` 函数）
```javascript
const processVoiceQueue = useCallback(() => {
  // 如果正在播放或队列为空，则返回
  if (isPlayingVoiceRef.current || voiceQueueRef.current.length === 0) {
    return;
  }

  // 取出队列首项并播放
  isPlayingVoiceRef.current = true;
  const item = voiceQueueRef.current.shift();
  
  // 播放音效 + 语音文件
  // 播放结束后：isPlayingVoiceRef.current = false
  // 然后延迟200ms后触发下一个（避免语音过于紧凑）
}, []);
```

#### 3. 修改 `playAiVoice` 函数
原本直接播放，现在改为**添加到队列**：
```javascript
voiceQueueRef.current.push({
  player,
  filename,
  audioPath,
  fullText,
  isPass
});
processVoiceQueue(); // 触发队列处理
```

#### 4. 清理机制
在游戏重置时（`resetMoveTracking`）清空队列：
```javascript
voiceQueueRef.current = [];
isPlayingVoiceRef.current = false;
if (currentAudioRef.current) {
  currentAudioRef.current.pause();
  currentAudioRef.current = null;
}
window.speechSynthesis?.cancel(); // 停止TTS
```

### 关键特性

1. **顺序保证**：队列中的语音严格按先进先出（FIFO）顺序播放
2. **间隔控制**：语音之间有 200ms 间隔，避免过于紧凑
3. **超时保护**：每个音频设置 5 秒超时，防止卡住
4. **TTS 兜底**：mp3 加载失败时自动降级到浏览器 TTS
5. **自动清理**：游戏结束/重新开始时自动清空队列

## 测试建议

### 测试场景
1. **快速连续出牌**：观察多个 AI 快速出牌时语音是否顺序播放
2. **网络延迟**：在慢速网络下测试语音是否仍保持顺序
3. **系统默认出牌**：轮到某个 AI 时后端自动出牌，检查语音播报
4. **游戏重置**：开始新游戏时，确认旧游戏的语音队列已清空

### 预期效果
- ✅ 语音按出牌顺序依次播放，不再重叠
- ✅ 即使网络延迟，语音顺序也不会错乱
- ✅ 每个语音之间有自然的间隔
- ✅ 游戏重置后旧语音立即停止

## 部署说明

已完成构建：
```bash
cd ui
npm run build
```

构建产物位于 `dist/` 目录，部署时需：
1. 将 `dist/` 下的所有文件上传到生产服务器
2. 确保 `/game/sounds/` 目录包含所有 162 个语音文件
3. 重启 Go 服务器以加载新的静态资源

## 相关文件

- `game/ui/src/App.jsx` - 主要修改文件
- `game/ui/generate_sounds.py` - 语音生成脚本
- `game/ui/public/sounds/` - 语音文件目录（162个mp3文件）

## 技术细节

### 队列数据结构
```javascript
voiceQueueRef.current = [
  {
    player: "RightBot",
    filename: "single_5",
    audioPath: "/game/sounds/RightBot/single_5.mp3",
    fullText: "下家出牌，单五",
    isPass: false
  },
  // ... 更多待播放项
]
```

### 状态流转
```
空闲状态 (isPlayingVoiceRef = false)
    ↓ 添加到队列
队列非空 → 触发 processVoiceQueue()
    ↓ 设置 isPlayingVoiceRef = true
播放音效 → 播放语音文件
    ↓ 监听 ended/error 事件
播放结束 → 设置 isPlayingVoiceRef = false
    ↓ 延迟 200ms
继续处理下一个 → processVoiceQueue()
```

## 已知限制

1. **浏览器 autoplay 限制**：首次交互前可能无法播放音频
2. **TTS 降级质量**：浏览器内置 TTS 质量不如 edge-tts 生成的文件
3. **移动端兼容性**：部分安卓设备的音频播放可能有延迟

## 历史背景

该修复基于以下历史问题：
- 原始语音文件由 edge-tts 批量生成（generate_sounds.py）
- 前端曾因 BASE_URL 路径问题导致 404 错误（已修复）
- 语音播放逻辑最初没有队列管理，导致多人出牌时混乱
