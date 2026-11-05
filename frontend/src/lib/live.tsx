"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { WS_URL } from "./api";
import type { NotificationItem, NotificationPrefsResponse, UnreadCount } from "./notification-types";
import type { ConnectionState, ScannerStatus, Signal, Ticker } from "./types";

export interface LiveKline {
  symbol: string;
  timeframe: string;
  open_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  confirmed: boolean;
}

type KlineListener = (k: LiveKline) => void;

function playChime() {
  try {
    const ctx = new AudioContext();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    gain.gain.value = 0.05;
    osc.frequency.value = 880;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.15);
    osc.onended = () => void ctx.close();
  } catch {
    // Browsers may refuse audio before the first interaction; the visual badge still updates.
  }
}

interface LiveContextValue {
  socket: "connecting" | "open" | "closed";
  tickers: Record<string, Ticker>;
  exchange: ConnectionState | null;
  scanner: ScannerStatus | null;
  lastSignal: Signal | null;
  subscribeKlines: (fn: KlineListener) => () => void;
}

const LiveContext = createContext<LiveContextValue | null>(null);

export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [socket, setSocket] = useState<LiveContextValue["socket"]>("connecting");
  const [tickers, setTickers] = useState<Record<string, Ticker>>({});
  const [exchange, setExchange] = useState<ConnectionState | null>(null);
  const [scanner, setScanner] = useState<ScannerStatus | null>(null);
  const [lastSignal, setLastSignal] = useState<Signal | null>(null);
  const listeners = useRef(new Set<KlineListener>());

  const subscribeKlines = useCallback((fn: KlineListener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ping: ReturnType<typeof setInterval> | undefined;

    const connect = () => {
      setSocket("connecting");
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        attempt = 0;
        setSocket("open");
        ping = setInterval(() => ws?.readyState === WebSocket.OPEN && ws.send("ping"), 25_000);
      };
      ws.onmessage = (e) => {
        let msg: { event: string; data: unknown };
        try {
          msg = JSON.parse(e.data);
        } catch {
          return;
        }
        switch (msg.event) {
          case "hello": {
            const d = msg.data as { connection: ConnectionState; tickers: Ticker[]; scanner: ScannerStatus; notifications?: UnreadCount };
            setExchange(d.connection);
            setScanner(d.scanner);
            setTickers(Object.fromEntries(d.tickers.map((t) => [t.symbol, t])));
            // Sent on every (re)connect: whatever was missed meanwhile is re-read from the database.
            if (d.notifications) qc.setQueryData(["notif-unread"], d.notifications);
            qc.invalidateQueries({ queryKey: ["notif"] });
            break;
          }
          case "ticker": {
            const t = msg.data as Ticker;
            setTickers((prev) => ({ ...prev, [t.symbol]: t }));
            break;
          }
          case "kline":
            listeners.current.forEach((fn) => fn(msg.data as LiveKline));
            break;
          case "connection":
            setExchange(msg.data as ConnectionState);
            break;
          case "scanner_status": {
            const s = msg.data as ScannerStatus;
            setScanner(s);
            setExchange(s.market);
            break;
          }
          case "analysis":
            qc.invalidateQueries({ queryKey: ["overview"] });
            qc.invalidateQueries({ queryKey: ["scanner"] });
            break;
          case "signal":
            setLastSignal(msg.data as Signal);
            qc.invalidateQueries({ queryKey: ["signals"] });
            qc.invalidateQueries({ queryKey: ["signal-stats"] });
            qc.invalidateQueries({ queryKey: ["candles"] });
            break;
          case "settings":
            qc.invalidateQueries({ queryKey: ["settings"] });
            qc.invalidateQueries({ queryKey: ["notif-prefs"] });
            break;
          case "notification": {
            const d = msg.data as { event: NotificationItem; unread: UnreadCount };
            qc.setQueryData(["notif-unread"], d.unread);
            qc.invalidateQueries({ queryKey: ["notif"] });
            if (qc.getQueryData<NotificationPrefsResponse>(["notif-prefs"])?.preferences.sound) playChime();
            break;
          }
          case "notification_read": {
            // Keeps other browser tabs in step with a read made anywhere.
            const d = msg.data as { unread: UnreadCount };
            qc.setQueryData(["notif-unread"], d.unread);
            qc.invalidateQueries({ queryKey: ["notif"] });
            break;
          }
        }
      };
      ws.onclose = () => {
        clearInterval(ping);
        setSocket("closed");
        if (closed) return;
        const delay = Math.min(30_000, 1000 * 2 ** attempt++);
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      clearInterval(ping);
      ws?.close();
    };
  }, [qc]);

  return (
    <LiveContext.Provider value={{ socket, tickers, exchange, scanner, lastSignal, subscribeKlines }}>
      {children}
    </LiveContext.Provider>
  );
}

export function useLive(): LiveContextValue {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error("useLive must be used inside LiveProvider");
  return ctx;
}

/** Live ticker if streamed, otherwise the fallback (e.g. from a REST query). */
export function useTicker(symbol: string, fallback?: Ticker | null): Ticker | null {
  const { tickers } = useLive();
  return tickers[symbol] ?? fallback ?? null;
}
