import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../config';

export const useDeviceWebSocket = (
  onMessage: (message: any) => void
) => {
  const wsRef = useRef<WebSocket | null>(null);
  const onMessageRef = useRef(onMessage);
  const reconnectTimeoutRef = useRef<number | null>(null);

  // Keep onMessage reference up to date
  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    const wsUrl = `${WS_BASE_URL}/devices`;
    console.log('[Device WebSocket] Connecting to:', wsUrl);
    
    const connect = () => {
      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.log('[Device WebSocket] Connected');
        // Clear any pending reconnect
        if (reconnectTimeoutRef.current !== null) {
          window.clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          console.log('[Device WebSocket] Message received:', message);
          onMessageRef.current(message);
        } catch (error) {
          console.error('[Device WebSocket] Failed to parse message:', error, event.data);
        }
      };

      ws.onerror = (error) => {
        console.error('[Device WebSocket] Error:', error);
      };

      ws.onclose = (event) => {
        console.log('[Device WebSocket] Disconnected', {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean
        });
        
        // Clean up
        wsRef.current = null;
        
        // If not normal close, try to reconnect
        if (event.code !== 1000) {
          console.log('[Device WebSocket] Attempting to reconnect in 3 seconds...');
          reconnectTimeoutRef.current = window.setTimeout(() => {
            if (wsRef.current === null || wsRef.current?.readyState === WebSocket.CLOSED) {
              connect();
            }
          }, 3000);
        }
      };

      wsRef.current = ws;
    };

    connect();

    return () => {
      console.log('[Device WebSocket] Cleaning up connection');
      if (reconnectTimeoutRef.current !== null) {
        window.clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.close(1000, 'Component unmounted');
      }
      wsRef.current = null;
    };
  }, []); // Only run once on mount
};

