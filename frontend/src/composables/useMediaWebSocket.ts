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

  function getContentHash(itemId: string): string {
    return itemId.split(':').pop() || itemId;
  }

  function movieQualityScore(m: Movie): number {
    let score = 0;
    if (m.info) score += 10;
    if (m.cover_path) score += 5;
    if (m.backdrop_path) score += 3;
    if (m.showreel_images?.length) score += 3;
    score += Object.keys(m.torrents || {}).length;
    return score;
  }

  function seriesQualityScore(s: Series): number {
    let score = 0;
    if (s.info) score += 10;
    if (s.cover_path) score += 5;
    if (s.backdrop_path) score += 3;
    const seasons = s.seasons || [];
    for (const season of seasons) {
      for (const ep of season.episodes || []) {
        score += Object.keys(ep.torrents || {}).length;
      }
    }
    return score;
  }

  function deduplicateMovies(items: Movie[]): Movie[] {
    const map = new Map<string, Movie>();
    for (const m of items) {
      const hash = getContentHash(m.id);
      const existing = map.get(hash);
      if (!existing || movieQualityScore(m) > movieQualityScore(existing)) {
        map.set(hash, m);
      }
    }
    return Array.from(map.values());
  }

  function deduplicateSeries(items: Series[]): Series[] {
    const map = new Map<string, Series>();
    for (const s of items) {
      const hash = getContentHash(s.id);
      const existing = map.get(hash);
      if (!existing || seriesQualityScore(s) > seriesQualityScore(existing)) {
        map.set(hash, s);
      }
    }
    return Array.from(map.values());
  }

  function buildIndex(): MediaIndex {
    const movies: Movie[] = [];
    const series: Series[] = [];
    for (const state of roots.value.values()) {
      movies.push(...state.movieMap.values());
      series.push(...state.seriesMap.values());
    }
    const dedupedMovies = deduplicateMovies(movies);
    const dedupedSeries = deduplicateSeries(series);
    return {
      version: 0,
      generated_at: new Date().toISOString(),
      stats: {
        total_movies: dedupedMovies.length,
        total_series: dedupedSeries.length,
      },
      movies: dedupedMovies,
      series: dedupedSeries,
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
