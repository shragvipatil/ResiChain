import { useEffect, useRef, useCallback, useState } from "react";
import { WebSocketEvent } from "../types";

export const useWebSocket = (onEvent: (event: WebSocketEvent) => void) => {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
 const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const connect = useCallback(() => {
    try {
      ws.current = new WebSocket("ws://localhost:8000/ws/agent-status");
      ws.current.onopen = () => setConnected(true);
      ws.current.onmessage = (msg) => {
        try {
          const event: WebSocketEvent = JSON.parse(msg.data);
          onEvent(event);
        } catch {
          console.error("Failed to parse WebSocket message");
        }
      };
      ws.current.onclose = () => {
        setConnected(false);
        reconnectTimer.current = setTimeout(connect, 3000);
      };
      ws.current.onerror = () => ws.current?.close();
    } catch {
      reconnectTimer.current = setTimeout(connect, 3000);
    }
  }, [onEvent]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
  }, [connect]);

  return { connected };
};