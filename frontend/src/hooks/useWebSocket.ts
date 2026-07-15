import { useEffect, useRef, useCallback, useState } from "react";
import { WebSocketEvent } from "../types";

export const useWebSocket = (onEvent: (event: WebSocketEvent) => void) => {
  const ws = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Always call the LATEST onEvent without the socket lifecycle depending
  // on its identity. Previously `connect` depended on `onEvent` and the
  // mount effect depended on `connect` — so every time the parent
  // component re-rendered and `onEvent` got a new function reference
  // (e.g. AppContext's handleWsEvent, which is recreated whenever its
  // own dependencies change), the effect's cleanup fired and closed the
  // socket mid-handshake, then immediately reopened a new one. Confirmed
  // in DevTools: "WebSocket is closed before the connection is
  // established" firing repeatedly. Using a ref here decouples "which
  // callback runs on message" from "when does the socket get torn down."
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const connect = useCallback(() => {
    try {
      ws.current = new WebSocket("ws://localhost:8000/ws/agent-status");
      ws.current.onopen = () => setConnected(true);
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
        reconnectTimer.current = setTimeout(connect, 3000);
      };
      ws.current.onerror = () => ws.current?.close();
    } catch {
      reconnectTimer.current = setTimeout(connect, 3000);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);   // stable — no longer depends on onEvent

  // Mount once. Connect once. Do not tear down/rebuild the socket just
  // because a parent re-rendered and handed us a new onEvent reference.
  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      ws.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);   // stable — runs once on mount, cleans up once on unmount

  return { connected };
};