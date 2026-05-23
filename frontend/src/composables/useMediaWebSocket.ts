import { ref, readonly, onUnmounted } from 'vue';
import type { Movie, Series, MediaIndex, TaskInfo, WsMessage } from '../types';

interface RootState {
  rootId: string;
  ws: WebSocket | null;
  movieMap: Map<string, Movie>;
  seriesMap: Map<string, Series>;
  connected: boolean;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
}

/**
 * Composable that connects to per-root MediaHive WebSockets and keeps
 * a merged media index updated in real time.
 *
 * The server sends per-root:
 *  - "init"   → full index (movies + series) on connect
 *  - "upsert" → single item inserted or updated
 *  - "remove" → single item removed
 *  - "task"   → background task progress
 */
export function useMediaWebSocket() {
  const mediaIndex = ref<MediaIndex | null>(null);
  const loading = ref(true);
  const error = ref<string | null>(null);
  const connected = ref(false);
  const tasks = ref<Map<string, TaskInfo>>(new Map());

  const roots = ref<Map<string, RootState>>(new Map());
  let disposed = false;

  function buildIndex(): MediaIndex {
    const movies: Movie[] = [];
    const series: Series[] = [];
    for (const state of roots.value.values()) {
      movies.push(...state.movieMap.values());
      series.push(...state.seriesMap.values());
    }
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

  function updateMergedState() {
    mediaIndex.value = buildIndex();
    // Loading is done when at least one root has connected and sent init
    let anyConnected = false;
    for (const state of roots.value.values()) {
      if (state.connected) {
        anyConnected = true;
        break;
      }
    }
    if (anyConnected) {
      loading.value = false;
      error.value = null;
    }
    connected.value = anyConnected;
  }

  function processJson(state: RootState, text: string) {
    const msg = JSON.parse(text) as WsMessage;

    switch (msg.type) {
      case 'init': {
        state.movieMap.clear();
        state.seriesMap.clear();
        for (const m of msg.data.movies) state.movieMap.set(m.id, m);
        for (const s of msg.data.series) state.seriesMap.set(s.id, s);
        updateMergedState();
        console.log(`[WS ${state.rootId}] init: ${state.movieMap.size} movies, ${state.seriesMap.size} series`);
        break;
      }
      case 'upsert': {
        if (msg.kind === 'movie') {
          state.movieMap.set(msg.item.id, msg.item as Movie);
        } else {
          state.seriesMap.set(msg.item.id, msg.item as Series);
        }
        updateMergedState();
        break;
      }
      case 'remove': {
        if (msg.kind === 'movie') {
          state.movieMap.delete(msg.id);
        } else {
          state.seriesMap.delete(msg.id);
        }
        updateMergedState();
        break;
      }
      case 'task': {
        const info = msg.data;
        if (info.status === 'completed' || info.status === 'cancelled' || info.status === 'error') {
          tasks.value.set(info.id, info);
          setTimeout(() => {
            tasks.value.delete(info.id);
            tasks.value = new Map(tasks.value);
          }, 3000);
        } else {
          tasks.value.set(info.id, info);
        }
        tasks.value = new Map(tasks.value);
        break;
      }
    }
  }

  function handleMessage(state: RootState, event: MessageEvent) {
    try {
      let text: string;
      if (event.data instanceof Blob) {
        event.data.text().then((t) => processJson(state, t));
        return;
      } else if (event.data instanceof ArrayBuffer) {
        text = new TextDecoder().decode(event.data);
      } else {
        text = event.data as string;
      }
      processJson(state, text);
    } catch (e) {
      console.error(`[WS ${state.rootId}] Failed to handle message:`, e);
    }
  }

  function connectRoot(rootId: string) {
    if (disposed) return;
    const existing = roots.value.get(rootId);
    if (existing?.ws) {
      // Already connecting or connected
      return;
    }

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/roots/${encodeURIComponent(rootId)}/ws`;

    const state: RootState = {
      rootId,
      ws: null,
      movieMap: new Map(),
      seriesMap: new Map(),
      connected: false,
      reconnectTimer: null,
    };
    roots.value.set(rootId, state);

    function doConnect() {
      if (disposed) return;
      console.log(`[WS ${rootId}] Connecting to ${url}...`);
      const ws = new WebSocket(url);
      state.ws = ws;

      ws.onopen = () => {
        state.connected = true;
        updateMergedState();
        console.log(`[WS ${rootId}] Connected`);
      };

      ws.onmessage = (ev) => handleMessage(state, ev);

      ws.onclose = (ev) => {
        state.connected = false;
        state.ws = null;
        updateMergedState();
        console.log(`[WS ${rootId}] Closed (code=${ev.code})`);
        scheduleReconnect();
      };

      ws.onerror = (ev) => {
        console.error(`[WS ${rootId}] Error:`, ev);
        if (!mediaIndex.value) {
          error.value = 'WebSocket connection failed';
        }
      };
    }

    function scheduleReconnect() {
      if (disposed) return;
      if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
      state.reconnectTimer = setTimeout(() => {
        console.log(`[WS ${rootId}] Reconnecting...`);
        doConnect();
      }, 2000);
    }

    doConnect();
  }

  function disconnectRoot(rootId: string) {
    const state = roots.value.get(rootId);
    if (!state) return;
    if (state.reconnectTimer) {
      clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
    if (state.ws) {
      state.ws.onclose = null;
      state.ws.close();
      state.ws = null;
    }
    state.connected = false;
    roots.value.delete(rootId);
    updateMergedState();
  }

  function setActiveRoots(rootIds: string[]) {
    if (disposed) return;
    const desired = new Set(rootIds);
    const current = new Set(roots.value.keys());

    // Add new roots
    for (const rid of desired) {
      if (!current.has(rid)) {
        connectRoot(rid);
      }
    }

    // Remove old roots
    for (const rid of current) {
      if (!desired.has(rid)) {
        disconnectRoot(rid);
      }
    }
  }

  function disconnect() {
    disposed = true;
    for (const state of roots.value.values()) {
      if (state.reconnectTimer) {
        clearTimeout(state.reconnectTimer);
      }
      if (state.ws) {
        state.ws.onclose = null;
        state.ws.close();
      }
    }
    roots.value.clear();
  }

  onUnmounted(disconnect);

  return {
    mediaIndex,
    loading: readonly(loading),
    error: readonly(error),
    connected: readonly(connected),
    tasks: readonly(tasks),
    setActiveRoots,
    disconnect,
  };
}
