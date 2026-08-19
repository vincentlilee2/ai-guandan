// 3.1 App.jsx 拆分：从单体组件中抽取的游戏实时状态流 hook。
// 封装 SSE（EventSource）连接管理：优先 SSE 实时推送，连接失败自动降级回轮询。
// 自身持有 eventSourceRef，不污染组件。依赖 fetchGameState（收到推送时复用其处理逻辑拉最新态）。
import { useCallback, useRef, useEffect } from 'react';

const MAX_RETRY = 3;

export function useGameStream(fetchGameState, getGameToken) {
  const eventSourceRef = useRef(null);
  // 始终指向最新的 fetchGameState，避免 connectGameStream 因依赖变化而重建，
  // 从而让 SSE 连接的生命周期只跟随 gameId 变化，不会反复重连。
  const fetchRef = useRef(fetchGameState);
  fetchRef.current = fetchGameState;
  // 始终指向最新的 getGameToken，保证 connectGameStream 引用稳定
  const tokenRef = useRef(getGameToken);
  tokenRef.current = getGameToken;

  // 主动断开当前 SSE：换局/新开一局时由 App 显式调用，
  // 防止旧局连接仍在订阅 wait_for_update，把旧局 finished 状态重放回新局。
  const disconnectGameStream = useCallback(() => {
    const es = eventSourceRef.current;
    if (es) {
      es._closedByUser = true;
      es.close();
      eventSourceRef.current = null;
    }
  }, []);

  const connectGameStream = useCallback((gid) => {
    if (typeof EventSource === 'undefined') {
      // 浏览器不支持 SSE，直接走轮询兜底
      fetchRef.current();
      return;
    }
    // 先停掉旧连接
    disconnectGameStream();
    let errCount = 0;
    const tok = tokenRef.current ? (tokenRef.current() || '') : '';
    const query = tok ? `?token=${encodeURIComponent(tok)}` : '';
    const es = new EventSource(`/api/${gid}/stream${query}`);
    eventSourceRef.current = es;
    es.onmessage = () => {
      try {
        // 后端已推送完整 state；此处直接复用现有拉取处理逻辑
        fetchRef.current();
      } catch (e) {
        console.warn('[SSE] 处理消息失败', e);
      }
    };
    es.onerror = () => {
      errCount++;
      es.close();
      // 仅当本连接仍是当前激活连接时才清理，避免误清换局后新建的连接
      if (eventSourceRef.current === es) {
        eventSourceRef.current = null;
      }
      // 被主动断开（换局/新开一局）的连接不再自动重连，避免旧 gid 连接复活；
      // 否则（普通网络抖动/连接被服务端关闭）进入重连/降级逻辑，保证数据通道不静默死亡。
      const isStale = es._closedByUser === true;
      if (errCount >= MAX_RETRY) {
        console.warn('[SSE] 连续失败，降级为轮询');
        fetchRef.current();
      } else if (!isStale) {
        setTimeout(() => connectGameStream(gid), 1000);
      }
    };
    // 依赖留空：fetchGameState 通过 fetchRef 读取，保证 connectGameStream 引用稳定；
    // disconnectGameStream 是稳定 useCallback([])，放入依赖不会导致重建
  }, [disconnectGameStream]);

  // 卸载时关闭连接，避免泄漏
  useEffect(() => () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  return { connectGameStream, disconnectGameStream, eventSourceRef };
}

export default useGameStream;
