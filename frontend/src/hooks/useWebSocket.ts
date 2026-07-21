import { useEffect, useRef, useCallback, useState } from "react";
import { WebSocketEvent } from "../types";

export const useWebSocket = (onEvent: (event: WebSocketEvent) => void) => {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  // true while the 3-second retry timer is running (i.e. was connected,
  // dropped, and is waiting to reconnect) — drives the "Reconnecting…"
  // indicator in AppHeader instead of jumping straight to "Offline".
  const [reconnecting, setReconnecting] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Always call the LATEST onEvent without the socket lifecycle depending
  // on its identity — prevents the socket from being torn down/rebuilt on
  // every parent re-render (confirmed cause of "WebSocket is closed before
  // the connection is established" firing repeatedly).
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const connect = useCallback(() => {
    try {
      ws.current = new WebSocket("ws://localhost:8000/ws/agent-status");
      ws.current.onopen = () => {
        setConnected(true);
        setReconnecting(false);
      };
      ws.current.onmessage = (msg) => {
        try {
          const event: WebSocketEvent = JSON.parse(msg.data);
          onEventRef.current(event);
        } catch {
          console.error("Failed to parse WebSocket message");
        }
      };
      ws.current.onclose = () => {
        setConnected(false);
        setReconnecting(true);
        reconnectTimer.current = setTimeout(connect, 3000);
      };
      ws.current.onerror = () => ws.current?.close();
    } catch {
      setReconnecting(true);
      reconnectTimer.current = setTimeout(connect, 3000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { connected, reconnecting };
};