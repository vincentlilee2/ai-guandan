// 3.1 App.jsx 拆分：音频播放逻辑抽到独立 hook。
// 设计：组件内被其它子系统（playSound/语音队列/ResultModal）共享的 ref 由调用方持有并传入，
// hook 只自持 audioContextRef / unlockAttemptedRef（仅本 hook 使用），避免重复声明导致状态分裂。
import { useRef, useCallback } from 'react'
import { PLAYER_DISPLAY_NAMES } from '../lib/playerNames'
import { SOUND_BASE } from '../lib/gameInit'

export function useAudio({
  audioUnlockedRef,      // 共享：unlockAudio 写，组件 1711 行读
  audioPoolRef,          // 共享：unlockAudio 预热，playSound 复用
  winCelebrationPlayedRef, // 共享：playWinCelebration 读名次
  winVoiceTimerRef,      // 共享：playWinCelebration 用，组件清理用
  gameOverVoiceTimerRef, // 共享：playGameOverSummary 用，ResultModal 用
}) {
  // 仅本 hook 使用的内部 ref
  const audioContextRef = useRef(null)
  const unlockAttemptedRef = useRef(false)

  // [Moved Up] unlockAudio 定义在这里，确保其他 useEffect 可以引用
  const unlockAudio = useCallback(() => {
    // 如果已经解锁或正在尝试解锁，直接返回
    if (audioUnlockedRef.current || unlockAttemptedRef.current) return
    unlockAttemptedRef.current = true

    // 如果没有 context 则创建
    if (!audioContextRef.current) {
      try {
        const AudioContext = window.AudioContext || window.webkitAudioContext
        if (AudioContext) {
          audioContextRef.current = new AudioContext()
        }
      } catch (e) { console.error(e) }
    }

    // 尝试 resume
    if (audioContextRef.current && audioContextRef.current.state === 'suspended') {
      audioContextRef.current.resume().catch(e => console.error(e))
    }

    // 1. 同时进行 global_voice_player 预热/解锁
    // [Fix] 必须解锁 audioPoolRef 中实际使用的那个 audio 对象，而不是 window 对象或临时对象
    try {
      let voicePlayer = audioPoolRef.current.get('global_voice_player')
      if (!voicePlayer) {
        voicePlayer = new Audio()
        audioPoolRef.current.set('global_voice_player', voicePlayer)
      }

      // 记录一下，方便调试
      window.global_voice_player = voicePlayer

      voicePlayer.volume = 0.01
      voicePlayer.src = `${SOUND_BASE}sounds/common/play_card.wav`

      const p = voicePlayer.play()
      if (p && typeof p.then === 'function') {
        p.then(() => {
          console.log('✅ Global Voice Player Unlocked')
          // 标记解锁成功
          audioUnlockedRef.current = true
          // 自然播放结束，不暂停
        }).catch((e) => {
          console.log('Voice unlock partial fail', e)
          // 失败了（可能是NotAllowed），允许下次重试
          unlockAttemptedRef.current = false
        })
      }

      // 2. 预加载 SFX (保留原有逻辑作为备份)
      const sfxAudio = new Audio(`${SOUND_BASE}sounds/common/play_card.wav`)
      sfxAudio.load()

    } catch (e) {
      console.error(e)
      unlockAttemptedRef.current = false
    }
  }, [audioUnlockedRef, audioPoolRef])

  // [新增] 合成欢呼音效 (Fanfare + Applause)
  const playSynthesizedCheer = useCallback(() => {
    try {
      if (!audioContextRef.current) {
        audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()
      }
      const ctx = audioContextRef.current
      if (ctx.state === 'suspended') ctx.resume()
      const t = ctx.currentTime

      const notes = [523.25, 659.25, 783.99, 1046.50]
      notes.forEach((freq, i) => {
        const osc = ctx.createOscillator()
        osc.type = 'triangle'
        osc.frequency.value = freq

        const gain = ctx.createGain()
        const startTime = t + i * 0.1
        gain.gain.setValueAtTime(0, startTime)
        gain.gain.linearRampToValueAtTime(0.2, startTime + 0.05)
        gain.gain.exponentialRampToValueAtTime(0.01, startTime + 1.0)

        osc.connect(gain)
        gain.connect(ctx.destination)
        osc.start(startTime)
        osc.stop(startTime + 1.2)
      })

      const bufferSize = ctx.sampleRate * 2.0
      const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate)
      const data = buffer.getChannelData(0)
      for (let i = 0; i < bufferSize; i++) {
        data[i] = (Math.random() * 2 - 1) * 0.5
      }

      const noise = ctx.createBufferSource()
      noise.buffer = buffer

      const filter = ctx.createBiquadFilter()
      filter.type = 'lowpass'
      filter.frequency.setValueAtTime(1000, t)
      filter.frequency.linearRampToValueAtTime(500, t + 2)

      const noiseGain = ctx.createGain()
      noiseGain.gain.setValueAtTime(0, t)
      noiseGain.gain.linearRampToValueAtTime(0.5, t + 0.2)
      noiseGain.gain.exponentialRampToValueAtTime(0.01, t + 2.0)

      noise.connect(filter)
      filter.connect(noiseGain)
      noiseGain.connect(ctx.destination)
      noise.start(t)

    } catch (e) {
      console.error('Synthesized Cheer failed:', e)
    }
  }, [])

  // [新增] 通用语音播报函数（小女孩音色）
  const speakWithGirlVoice = useCallback((text) => {
    try {
      window.speechSynthesis.cancel()
      const u = new SpeechSynthesisUtterance(text)
      u.lang = 'zh-CN'
      u.rate = 1.1
      u.pitch = 1.6

      const voices = window.speechSynthesis.getVoices()
      const targetVoice = voices.find(v => v.name.includes('Yaoyao') || v.name.includes('Xiaoxiao') || v.name.includes('Yating'))
        || voices.find(v => v.name.includes('Huihui') || v.name.includes('Meijia'))
        || voices.find(v => v.name.includes('Female') || v.name.includes('Girl'))
        || voices.find(v => v.lang.includes('zh'))

      if (targetVoice) {
        u.voice = targetVoice
        console.log('Selected voice:', targetVoice.name)
      }
      window.speechSynthesis.speak(u)
    } catch (e) {
      console.error('Speech synthesis failed:', e)
    }
  }, [])

  // [新增] 播放玩家获胜（完牌）的庆祝音效 + 语音
  const playWinCelebration = useCallback(async (player) => {
    console.log(`播放获胜庆祝: ${player}`)
    if (!audioUnlockedRef.current) unlockAudio()

    try {
      playSynthesizedCheer()

      if (winVoiceTimerRef.current) clearTimeout(winVoiceTimerRef.current)

      winVoiceTimerRef.current = setTimeout(async () => {
        const isMe = player === 'User'
        if (isMe) {
          const finishedList = Array.from(winCelebrationPlayedRef.current)
          const userIdx = finishedList.indexOf('User')
          const rank = userIdx !== -1 ? userIdx + 1 : finishedList.length

          const rankFile = `${SOUND_BASE}sounds/User/congrat_rank${rank}.mp3`
          const audio = new Audio(rankFile)
          audio.play().catch(err => {
            console.warn('Play rank mp3 failed, fallback to TTS:', err)
            const text = rank === 1 ? '恭喜你获得第一名！' : (rank === 2 ? '恭喜你获得第二名！' : '恭喜你获得第三名！')
            speakWithGirlVoice(text)
          })
        } else {
          const name = PLAYER_DISPLAY_NAMES[player] || player
          const text = `${name} 胜出！`
          speakWithGirlVoice(text)
        }
        winVoiceTimerRef.current = null
      }, 1500)
    } catch (e) {
      console.error('Play win celebration failed', e)
    }
  }, [unlockAudio, playSynthesizedCheer, speakWithGirlVoice, winCelebrationPlayedRef, winVoiceTimerRef, audioUnlockedRef])

  // [新增] 播放游戏结束 summary 语音
  const playGameOverSummary = useCallback(async (resultData) => {
    console.log('播放游戏结算总结:', resultData)
    if (!audioUnlockedRef.current) unlockAudio()

    const { scores, info } = resultData || {}
    const scoreVal = scores?.['User'] || 0
    const baseVal = info?.base || 100

    try {
      let audioFile = ''
      let fallbackText = ''

      // 文案用「名次版」（第一名/第X名 + 你们各得），与重新生成的 sounds/User/end_*.mp3 一致：
      // 「一三游/头游」等术语在 TTS 连读下易被听成别的音节，故用名次展开替代
      if (scoreVal > 0) {
        if (baseVal === 300) {
          audioFile = `${SOUND_BASE}sounds/User/end_team_300.mp3`
          fallbackText = `本局结束。你和队友获得第一名和第二名。你们各得${scoreVal}分。`
        } else if (baseVal === 200) {
          audioFile = `${SOUND_BASE}sounds/User/end_team_200.mp3`
          fallbackText = `本局结束。你和队友获得第一名和第三名。你们各得${scoreVal}分。`
        } else {
          audioFile = `${SOUND_BASE}sounds/User/end_team_100.mp3`
          fallbackText = `本局结束。你和队友获得第一名和第四名。你们各得${scoreVal}分。`
        }
      } else if (scoreVal < 0) {
        const absScore = Math.abs(scoreVal)
        if (baseVal === 300) {
          audioFile = `${SOUND_BASE}sounds/User/end_lose_300.mp3`
          fallbackText = `本局结束。对手获得第一名和第二名。你们本局失败，扣${absScore}分。`
        } else if (baseVal === 200) {
          audioFile = `${SOUND_BASE}sounds/User/end_lose_200.mp3`
          fallbackText = `本局结束。对手获得第一名和第三名。你们本局失败，扣${absScore}分。`
        } else {
          audioFile = `${SOUND_BASE}sounds/User/end_lose_100.mp3`
          fallbackText = `本局结束。对手获得第一名和第四名。你们本局失败，扣${absScore}分。`
        }
      } else {
        fallbackText = '本局结束。双方平局。'
      }

      if (gameOverVoiceTimerRef.current) clearTimeout(gameOverVoiceTimerRef.current)

      gameOverVoiceTimerRef.current = setTimeout(() => {
        const hasMultiplier = scoreVal !== 0 && Math.abs(scoreVal) !== baseVal
        if (audioFile && !hasMultiplier) {
          const audio = new Audio(audioFile)
          audio.play().catch(err => {
            console.warn('Play summary mp3 failed, fallback to TTS:', err)
            speakWithGirlVoice(fallbackText)
          })
        } else if (fallbackText) {
          speakWithGirlVoice(fallbackText)
        }
        gameOverVoiceTimerRef.current = null
      }, 1500)
    } catch (e) {
      console.error('Game Over Speech failed:', e)
    }
  }, [unlockAudio, speakWithGirlVoice, gameOverVoiceTimerRef, audioUnlockedRef])

  // [新增] 合成爆炸音效（炸弹）
  const playExplosionSound = useCallback(() => {
    try {
      if (!audioContextRef.current) {
        audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)()
      }
      const ctx = audioContextRef.current
      if (ctx.state === 'suspended') ctx.resume()
      const t = ctx.currentTime

      const bufferSize = ctx.sampleRate * 2.5
      const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate)
      const data = buffer.getChannelData(0)
      for (let i = 0; i < bufferSize; i++) {
        data[i] = (Math.random() * 2 - 1)
      }

      const noise = ctx.createBufferSource()
      noise.buffer = buffer
      const noiseFilter = ctx.createBiquadFilter()
      noiseFilter.type = 'lowpass'
      noiseFilter.frequency.setValueAtTime(800, t)
      noiseFilter.frequency.exponentialRampToValueAtTime(100, t + 0.8)

      const noiseGain = ctx.createGain()
      noiseGain.gain.setValueAtTime(1.5, t)
      noiseGain.gain.exponentialRampToValueAtTime(0.001, t + 1.2)

      noise.connect(noiseFilter)
      noiseFilter.connect(noiseGain)
      noiseGain.connect(ctx.destination)
      noise.start(t)
      noise.stop(t + 2.0)

      const osc = ctx.createOscillator()
      osc.type = 'sawtooth'
      osc.frequency.setValueAtTime(60, t)
      osc.frequency.exponentialRampToValueAtTime(10, t + 1.5)

      const oscGain = ctx.createGain()
      oscGain.gain.setValueAtTime(0.8, t)
      oscGain.gain.exponentialRampToValueAtTime(0.001, t + 1.2)

      const distortion = ctx.createWaveShaper()
      const curve = new Float32Array(44100)
      for (let i = 0; i < 44100; ++i) {
        const x = (i * 2) / 44100 - 1
        curve[i] = (Math.PI + 5) * x / (Math.PI + 50 * Math.abs(x))
      }
      distortion.curve = curve

      osc.connect(distortion)
      distortion.connect(oscGain)
      oscGain.connect(ctx.destination)
      osc.start(t)
      osc.stop(t + 2.0)
    } catch (e) {
      console.error('Explosion synth failed:', e)
    }
  }, [])

  return {
    unlockAudio,
    playSynthesizedCheer,
    speakWithGirlVoice,
    playWinCelebration,
    playGameOverSummary,
    playExplosionSound,
  }
}

export default useAudio
