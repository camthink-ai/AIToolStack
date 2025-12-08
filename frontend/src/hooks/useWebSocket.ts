import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../config';

export const useWebSocket = (
  projectId: string,
  onMessage: (message: any) => void
) => {
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!projectId) return;

    const wsUrl = `${WS_BASE_URL}/projects/${projectId}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('[WebSocket] Connected to project', projectId);
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        onMessage(message);
      } catch (error) {
        console.error('[WebSocket] Failed to parse message:', error);
      }
    };

    ws.onerror = (error) => {
      console.error('[WebSocket] Error:', error);
    };

    ws.onclose = () => {
      console.log('[WebSocket] Disconnected');
      // 尝试重连
      setTimeout(() => {
        if (wsRef.current?.readyState === WebSocket.CLOSED) {
          // 重新连接逻辑可以在这里实现
        }
      }, 3000);
    };

    wsRef.current = ws;

    return () => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }, [projectId, onMessage]);
};

