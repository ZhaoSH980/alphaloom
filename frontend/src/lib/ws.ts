// frontend/src/lib/ws.ts
export interface RunEvent { type: string; [k: string]: any; }
export function openRunSocket(runId: string, onEvent: (e: RunEvent) => void,
                              onClose?: () => void) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/runs/${runId}`);
  ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch { /* ignore */ } };
  ws.onclose = () => onClose?.();
  return {
    send: (cmd: "resume" | "step" | "stop") => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ cmd }));
    },
    close: () => ws.close(),
  };
}
