# 截图 / 演示素材

README 中引用的图片放在这里。

## 需要的素材

| 文件名 | 内容 | 建议尺寸 |
|---|---|---|
| `screenshot-table.png` | 牌桌全景：四家、手牌、出牌气泡 | 1280×720 |
| `screenshot-thinking.png` | AI 思考中（头像转圈 + 思考过程文字） | 1280×720 |
| `demo.gif` | 一轮完整出牌的动图（8~15 秒） | ≤ 5MB |

## 采集方法（macOS）

```bash
# 1. 启动服务
./start_dev.sh          # 打开 http://127.0.0.1:3011

# 2. 静态截图：Cmd+Shift+4 然后按空格，点击浏览器窗口
#    存到本目录并按上表命名

# 3. 录 GIF：先用系统录屏 Cmd+Shift+5 录制 mp4，再转 GIF
ffmpeg -i demo.mp4 -vf "fps=12,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" -loop 0 demo.gif

# 控制体积（GitHub README 建议 < 5MB）
ls -lh demo.gif
```

## 注意

- 截图前把浏览器窗口调到 16:9，避免出现书签栏等无关内容
- 不要拍到含真实 API Key 的开发者工具面板
- 深色背景的牌桌在 GitHub 明/暗两种主题下都好看
