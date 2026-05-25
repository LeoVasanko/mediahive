import { ref, readonly, onUnmounted } from "vue"
import type {
  Movie,
  Series,
  Episode,
  Season,
  Torrent,
  MediaIndex,
  TaskInfo,
  WsMessage,
} from "../types"

interface RootState {
  rootId: string
  ws: WebSocket | null
  movieMap: Map<string, Movie>
  seriesMap: Map<string, Series>
  connected: boolean
  reconnectTimer: ReturnType<typeof setTimeout> | null
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
  const mediaIndex = ref<MediaIndex | null>(null)
  const loading = ref(true)
  const error = ref<string | null>(null)
  const connected = ref(false)
  const tasks = ref<Map<string, TaskInfo>>(new Map())

  const roots = ref<Map<string, RootState>>(new Map())
  let disposed = false

  function getContentHash(itemId: string): string {
    return itemId.split(":").pop() || itemId
  }

  function torrentQualityScore(t: Torrent): number {
    let score = 0
    const res = (t.resolution || "").toLowerCase()
    if (res.includes("2160") || res.includes("4k") || res.includes("uhd")) score += 100
    else if (res.includes("1080") || res.includes("fhd")) score += 80
    else if (res.includes("720") || res === "hd") score += 60
    else if (res.includes("480") || res === "sd") score += 40
    else if (res.includes("360")) score += 20
    if (t.has_dolby_vision) score += 15
    if (t.is_hdr) score += 10
    if (t.has_dolby_atmos) score += 5
    return score
  }

  function annotateTorrents(
    torrents: { [key: string]: Torrent },
    rootId: string | null,
  ): { [key: string]: Torrent } {
    return Object.fromEntries(
      Object.entries(torrents || {}).map(([k, t]) => [k, { ...t, root_id: t.root_id || rootId }]),
    )
  }

  function mergeTorrentDicts(
    a: { [key: string]: Torrent },
    b: { [key: string]: Torrent },
  ): { [key: string]: Torrent } {
    const merged: { [key: string]: Torrent } = { ...a }
    for (const [k, t] of Object.entries(b)) {
      const uniqueKey = merged[k] ? `${t.root_id || "unknown"}:${k}` : k
      merged[uniqueKey] = t
    }
    const sorted = Object.entries(merged).sort(([, t1], [, t2]) => {
      const s1 = torrentQualityScore(t1)
      const s2 = torrentQualityScore(t2)
      if (s2 !== s1) return s2 - s1
      return (t2.size || 0) - (t1.size || 0)
    })
    return Object.fromEntries(sorted)
  }

  function mergeMovies(a: Movie, b: Movie): Movie {
    const torrentsA = annotateTorrents(a.torrents, a.root_id)
    const torrentsB = annotateTorrents(b.torrents, b.root_id)
    return {
      ...a,
      id: getContentHash(a.id),
      torrents: mergeTorrentDicts(torrentsA, torrentsB),
      info: a.info || b.info,
      cover_path: a.cover_path || b.cover_path,
      backdrop_path: a.backdrop_path || b.backdrop_path,
      showreel_images: a.showreel_images?.length ? a.showreel_images : b.showreel_images,
      showreel_source_sets: a.showreel_source_sets?.length
        ? a.showreel_source_sets
        : b.showreel_source_sets,
    }
  }

  function mergeEpisodes(
    a: Episode,
    b: Episode,
    rootIdA: string | null,
    rootIdB: string | null,
  ): Episode {
    const torrentsA = annotateTorrents(a.torrents, rootIdA)
    const torrentsB = annotateTorrents(b.torrents, rootIdB)
    return {
      ...a,
      torrents: mergeTorrentDicts(torrentsA, torrentsB),
      reel_image: a.reel_image || b.reel_image,
      reel_sources: a.reel_sources?.length ? a.reel_sources : b.reel_sources,
    }
  }

  function mergeSeasons(
    a: Season,
    b: Season,
    rootIdA: string | null,
    rootIdB: string | null,
  ): Season {
    const episodeMap = new Map<number, Episode>()
    for (const ep of a.episodes) {
      episodeMap.set(ep.episode_number, ep)
    }
    for (const ep of b.episodes) {
      const existing = episodeMap.get(ep.episode_number)
      if (existing) {
        episodeMap.set(ep.episode_number, mergeEpisodes(existing, ep, rootIdA, rootIdB))
      } else {
        episodeMap.set(ep.episode_number, {
          ...ep,
          torrents: annotateTorrents(ep.torrents, rootIdB),
        })
      }
    }
    return {
      ...a,
      episodes: Array.from(episodeMap.values()).sort((a, b) => a.episode_number - b.episode_number),
      poster_path: a.poster_path || b.poster_path,
    }
  }

  function mergeSeries(a: Series, b: Series): Series {
    const seasonMap = new Map<number, Season>()
    for (const season of a.seasons || []) {
      seasonMap.set(season.season_number, {
        ...season,
        episodes: season.episodes.map((ep) => ({
          ...ep,
          torrents: annotateTorrents(ep.torrents, a.root_id),
        })),
      })
    }
    for (const season of b.seasons || []) {
      const existing = seasonMap.get(season.season_number)
      if (existing) {
        seasonMap.set(season.season_number, mergeSeasons(existing, season, a.root_id, b.root_id))
      } else {
        seasonMap.set(season.season_number, {
          ...season,
          episodes: season.episodes.map((ep) => ({
            ...ep,
            torrents: annotateTorrents(ep.torrents, b.root_id),
          })),
        })
      }
    }
    return {
      ...a,
      id: getContentHash(a.id),
      seasons: Array.from(seasonMap.values()).sort((a, b) => a.season_number - b.season_number),
      info: a.info || b.info,
      cover_path: a.cover_path || b.cover_path,
      backdrop_path: a.backdrop_path || b.backdrop_path,
    }
  }

  function mergeItemsByHash<T extends Movie | Series>(items: T[], mergeFn: (a: T, b: T) => T): T[] {
    const map = new Map<string, T[]>()
    for (const item of items) {
      const hash = getContentHash(item.id)
      const arr = map.get(hash) || []
      arr.push(item)
      map.set(hash, arr)
    }
    const merged: T[] = []
    for (const [, group] of map) {
      if (group.length === 1) {
        merged.push(group[0])
      } else {
        let result = group[0]
        for (let i = 1; i < group.length; i++) {
          result = mergeFn(result, group[i])
        }
        merged.push(result)
      }
    }
    return merged
  }

  function buildIndex(): MediaIndex {
    const movies: Movie[] = []
    const series: Series[] = []
    for (const state of roots.value.values()) {
      movies.push(...state.movieMap.values())
      series.push(...state.seriesMap.values())
    }
    const mergedMovies = mergeItemsByHash(movies, mergeMovies)
    const mergedSeries = mergeItemsByHash(series, mergeSeries)
    return {
      version: 0,
      generated_at: new Date().toISOString(),
      stats: {
        total_movies: mergedMovies.length,
        total_series: mergedSeries.length,
      },
      movies: mergedMovies,
      series: mergedSeries,
    }
  }

  function updateMergedState() {
    mediaIndex.value = buildIndex()
    // Loading is done when at least one root has connected and sent init
    let anyConnected = false
    for (const state of roots.value.values()) {
      if (state.connected) {
        anyConnected = true
        break
      }
    }
    if (anyConnected) {
      loading.value = false
      error.value = null
    }
    connected.value = anyConnected
  }

  function processJson(state: RootState, text: string) {
    const msg = JSON.parse(text) as WsMessage

    switch (msg.type) {
      case "init": {
        state.movieMap.clear()
        state.seriesMap.clear()
        for (const m of msg.data.movies) state.movieMap.set(m.id, m)
        for (const s of msg.data.series) state.seriesMap.set(s.id, s)
        updateMergedState()
        console.log(
          `[WS ${state.rootId}] init: ${state.movieMap.size} movies, ${state.seriesMap.size} series`,
        )
        break
      }
      case "upsert": {
        if (msg.kind === "movie") {
          state.movieMap.set(msg.item.id, msg.item as Movie)
        } else {
          state.seriesMap.set(msg.item.id, msg.item as Series)
        }
        updateMergedState()
        break
      }
      case "remove": {
        if (msg.kind === "movie") {
          state.movieMap.delete(msg.id)
        } else {
          state.seriesMap.delete(msg.id)
        }
        updateMergedState()
        break
      }
      case "task": {
        const info = msg.data
        if (info.status === "completed" || info.status === "cancelled" || info.status === "error") {
          tasks.value.set(info.id, info)
          setTimeout(() => {
            tasks.value.delete(info.id)
            tasks.value = new Map(tasks.value)
          }, 3000)
        } else {
          tasks.value.set(info.id, info)
        }
        tasks.value = new Map(tasks.value)
        break
      }
    }
  }

  function handleMessage(state: RootState, event: MessageEvent) {
    try {
      let text: string
      if (event.data instanceof Blob) {
        event.data.text().then((t) => processJson(state, t))
        return
      } else if (event.data instanceof ArrayBuffer) {
        text = new TextDecoder().decode(event.data)
      } else {
        text = event.data as string
      }
      processJson(state, text)
    } catch (e) {
      console.error(`[WS ${state.rootId}] Failed to handle message:`, e)
    }
  }

  function connectRoot(rootId: string) {
    if (disposed) return
    const existing = roots.value.get(rootId)
    if (existing?.ws) {
      // Already connecting or connected
      return
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:"
    const url = `${proto}//${location.host}/api/roots/${encodeURIComponent(rootId)}/ws`

    const state: RootState = {
      rootId,
      ws: null,
      movieMap: new Map(),
      seriesMap: new Map(),
      connected: false,
      reconnectTimer: null,
    }
    roots.value.set(rootId, state)

    function doConnect() {
      if (disposed) return
      console.log(`[WS ${rootId}] Connecting to ${url}...`)
      const ws = new WebSocket(url)
      state.ws = ws

      ws.onopen = () => {
        state.connected = true
        updateMergedState()
        console.log(`[WS ${rootId}] Connected`)
      }

      ws.onmessage = (ev) => handleMessage(state, ev)

      ws.onclose = (ev) => {
        state.connected = false
        state.ws = null
        updateMergedState()
        console.log(`[WS ${rootId}] Closed (code=${ev.code})`)
        scheduleReconnect()
      }

      ws.onerror = (ev) => {
        console.error(`[WS ${rootId}] Error:`, ev)
        if (!mediaIndex.value) {
          error.value = "WebSocket connection failed"
        }
      }
    }

    function scheduleReconnect() {
      if (disposed) return
      if (state.reconnectTimer) clearTimeout(state.reconnectTimer)
      state.reconnectTimer = setTimeout(() => {
        console.log(`[WS ${rootId}] Reconnecting...`)
        doConnect()
      }, 2000)
    }

    doConnect()
  }

  function disconnectRoot(rootId: string) {
    const state = roots.value.get(rootId)
    if (!state) return
    if (state.reconnectTimer) {
      clearTimeout(state.reconnectTimer)
      state.reconnectTimer = null
    }
    if (state.ws) {
      state.ws.onclose = null
      state.ws.close()
      state.ws = null
    }
    state.connected = false
    roots.value.delete(rootId)
    updateMergedState()
  }

  function setActiveRoots(rootIds: string[]) {
    if (disposed) return
    const desired = new Set(rootIds)
    const current = new Set(roots.value.keys())

    // Add new roots
    for (const rid of desired) {
      if (!current.has(rid)) {
        connectRoot(rid)
      }
    }

    // Remove old roots
    for (const rid of current) {
      if (!desired.has(rid)) {
        disconnectRoot(rid)
      }
    }
  }

  function disconnect() {
    disposed = true
    for (const state of roots.value.values()) {
      if (state.reconnectTimer) {
        clearTimeout(state.reconnectTimer)
      }
      if (state.ws) {
        state.ws.onclose = null
        state.ws.close()
      }
    }
    roots.value.clear()
  }

  onUnmounted(disconnect)

  return {
    mediaIndex,
    loading: readonly(loading),
    error: readonly(error),
    connected: readonly(connected),
    tasks: readonly(tasks),
    setActiveRoots,
    disconnect,
  }
}
