import { ref, readonly, onUnmounted } from 'vue';
import type { Movie, Series, MediaIndex, TaskInfo, WsMessage } from '../types';

/**
 * Composable that connects to the MediaHive WebSocket and keeps
 * the media index updated in real time.
 *
 * The server sends:
 *  - "init"   → full index (movies + series) on connect
 *  - "upsert" → single item inserted or updated
 *  - "remove" → single item removed
 *  - "task"   → background task progress
 *
 * Messages are msgspec-encoded binary JSON with a "type" tag field.
 */
export function useMediaWebSocket() {
  const mediaIndex = ref<MediaIndex | null>(null);
  const loading = ref(true);
  const error = ref<string | null>(null);
  const connected = ref(false);
  const tasks = ref<Map<string, TaskInfo>>(new Map());

  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let disposed = false;

  // Lookup maps for fast upsert / remove
  const movieMap = new Map<string, Movie>();
  const seriesMap = new Map<string, Series>();

  function buildIndex(): MediaIndex {
    const movies = Array.from(movieMap.values());
    const series = Array.from(seriesMap.values());
    return {
      version: 0,
      generated_at: new Date().toISOString(),
      stats: {
        total_movies: movies.length,
        total_series: series.length,
      },
      movies,
      series,
    };
  }

  function handleMessage(event: MessageEvent) {
    try {
      // Server sends binary frames (msgspec json bytes)
      let text: string;
      if (event.data instanceof Blob) {
        // Will be handled by the blob reader below
        event.data.text().then((t) => processJson(t));
        return;
      } else if (event.data instanceof ArrayBuffer) {
        text = new TextDecoder().decode(event.data);
      } else {
        text = event.data as string;
      }
      processJson(text);
    } catch (e) {
      console.error('[WS] Failed to handle message:', e);
    }
  }

  function processJson(text: string) {
    const msg = JSON.parse(text) as WsMessage;

    switch (msg.type) {
      case 'init': {
        movieMap.clear();
        seriesMap.clear();
        for (const m of msg.data.movies) movieMap.set(m.id, m);
        for (const s of msg.data.series) seriesMap.set(s.id, s);
        mediaIndex.value = buildIndex();
        loading.value = false;
        error.value = null;
        console.log(`[WS] init: ${movieMap.size} movies, ${seriesMap.size} series`);
        break;
      }
      case 'upsert': {
        if (msg.kind === 'movie') {
          movieMap.set(msg.item.id, msg.item as Movie);
        } else {
          seriesMap.set(msg.item.id, msg.item as Series);
        }
        // Rebuild the index ref so Vue detects the change
        mediaIndex.value = buildIndex();
        break;
      }
      case 'remove': {
        if (msg.kind === 'movie') {
          movieMap.delete(msg.id);
        } else {
          seriesMap.delete(msg.id);
        }
        mediaIndex.value = buildIndex();
        break;
      }
      case 'task': {
        const info = msg.data;
        if (info.status === 'completed' || info.status === 'cancelled' || info.status === 'error') {
          // Keep finished tasks briefly so the UI can show completion
          tasks.value.set(info.id, info);
          setTimeout(() => {
            tasks.value.delete(info.id);
            tasks.value = new Map(tasks.value);
          }, 3000);
        } else {
          tasks.value.set(info.id, info);
        }
        // Trigger reactivity
        tasks.value = new Map(tasks.value);
        break;
      }
    }
  }

  function connect() {
    if (disposed) return;

    // Build WS URL relative to current page
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/ws`;

    console.log(`[WS] Connecting to ${url}...`);
    ws = new WebSocket(url);

    ws.onopen = () => {
      connected.value = true;
      error.value = null;
      console.log('[WS] Connected');
    };

    ws.onmessage = handleMessage;

    ws.onclose = (ev) => {
      connected.value = false;
      console.log(`[WS] Closed (code=${ev.code})`);
      scheduleReconnect();
    };

    ws.onerror = (ev) => {
      console.error('[WS] Error:', ev);
      if (!mediaIndex.value) {
        error.value = 'WebSocket connection failed';
      }
    };
  }

  function scheduleReconnect() {
    if (disposed) return;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      console.log('[WS] Reconnecting...');
      connect();
    }, 2000);
  }

  function disconnect() {
    disposed = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.onclose = null; // prevent reconnect
      ws.close();
      ws = null;
    }
  }

  // Start the connection
  connect();

  // Clean up on component unmount
  onUnmounted(disconnect);

  return {
    mediaIndex,
    loading: readonly(loading),
    error: readonly(error),
    connected: readonly(connected),
    tasks: readonly(tasks),
    disconnect,
  };
}
