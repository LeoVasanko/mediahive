import { shallowRef, readonly, onUnmounted } from "vue"
import type {
  Movie,
  MovieUi,
  Person,
  Series,
  SeriesUi,
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
  movieMap: Map<string, MovieUi>
  seriesMap: Map<string, SeriesUi>
  peopleMap: Map<number, Person>
  connected: boolean
  initialized: boolean
  pendingMessages: WsMessage[]
  reconnectTimer: ReturnType<typeof setTimeout> | null
}

const MERGED_KEY_DELIMITER = "::"

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
  const mediaIndex = shallowRef<MediaIndex | null>(null)
  const loading = shallowRef(true)
  const error = shallowRef<string | null>(null)
  const connected = shallowRef(false)
  const tasks = shallowRef<Map<string, TaskInfo>>(new Map())

  const roots = shallowRef<Map<string, RootState>>(new Map())
  let disposed = false

  // Single periodic sweep for completed tasks instead of one timeout per task
  const completedTaskIds = new Set<string>()
  let taskSweepTimer: ReturnType<typeof setInterval> | null = null
  function startTaskSweep() {
    if (taskSweepTimer !== null) return
    taskSweepTimer = setInterval(() => {
      if (completedTaskIds.size === 0) return
      const next = new Map(tasks.value)
      let changed = false
      for (const id of completedTaskIds) {
        if (next.delete(id)) changed = true
      }
      completedTaskIds.clear()
      if (changed) {
        tasks.value = next
      }
    }, 3000)
  }
  function stopTaskSweep() {
    if (taskSweepTimer !== null) {
      clearInterval(taskSweepTimer)
      taskSweepTimer = null
    }
  }
  onUnmounted(stopTaskSweep)

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
    if (t.dovi) score += 15
    if (t.hdr) score += 10
    if (t.atmos) score += 5
    return score
  }

  function expandPlayablePath(fileKey: string, playableFile: string | null): string | null {
    if (!playableFile) return fileKey
    if (playableFile.startsWith("concat:") || playableFile.includes("://")) return playableFile
    if (playableFile.startsWith(`${fileKey}/`)) return playableFile
    if (playableFile.startsWith("/")) return playableFile.replace(/^\/+/, "")
    return `${fileKey}/${playableFile}`
  }

  function annotateFiles(
    files: { [key: string]: Torrent },
    rootId: string | null,
  ): { [key: string]: Torrent } {
    return Object.fromEntries(
      Object.entries(files || {}).map(([k, t]) => [
        k,
        {
          ...t,
          playable_file: expandPlayablePath(resolveFilePathKey(k), t.playable_file || null),
          root_id: t.root_id || rootId,
        },
      ]),
    )
  }

  function resolveFilePathKey(key: string): string {
    const delimIndex = key.indexOf(MERGED_KEY_DELIMITER)
    if (delimIndex >= 0) {
      return key.slice(delimIndex + MERGED_KEY_DELIMITER.length)
    }
    return key
  }

  function mergeTorrentDicts(
    a: { [key: string]: Torrent },
    b: { [key: string]: Torrent },
  ): { [key: string]: Torrent } {
    const merged: { [key: string]: Torrent } = { ...a }
    for (const [k, t] of Object.entries(b)) {
      const uniqueKey = merged[k] ? `${t.root_id || "unknown"}${MERGED_KEY_DELIMITER}${k}` : k
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

  function withMovieIdentity(
    id: string,
    movie: Movie,
    rootId: string,
    people: Map<number, Person>,
  ): MovieUi {
    return { ...normalizeMovie(movie, rootId, people), id, root_id: rootId }
  }

  function withSeriesIdentity(
    id: string,
    series: Series,
    rootId: string,
    people: Map<number, Person>,
  ): SeriesUi {
    return { ...normalizeSeries(series, rootId, people), id, root_id: rootId }
  }

  function normalizeCastMember(
    member: unknown,
    people: Map<number, Person>,
  ): {
    name: string
    character: string | null
    profile_path: string | null
    gender: string | null
    id: number | null
  } {
    if (!Array.isArray(member)) {
      return {
        name: "",
        character: null,
        profile_path: null,
        gender: null,
        id: null,
      }
    }

    // Current wire format: CastCredit(array_like=True) => [character, id]
    const character = typeof member[0] === "string" ? member[0] : null
    const id = typeof member[1] === "number" ? member[1] : null
    const person = id !== null ? people.get(id) || null : null
    return {
      name: person?.name || "",
      character,
      profile_path: person?.profile_path || null,
      gender: person?.gender || null,
      id,
    }
  }

  function normalizeSimilarMember(member: unknown): { id: number; title: string } {
    if (!Array.isArray(member)) {
      return { id: 0, title: "" }
    }
    return {
      id: typeof member[0] === "number" ? member[0] : 0,
      title: typeof member[1] === "string" ? member[1] : "",
    }
  }

  function normalizePerson(member: unknown): Person | null {
    if (!Array.isArray(member)) return null
    return {
      name: typeof member[0] === "string" ? member[0] : "",
      profile_path: typeof member[1] === "string" ? member[1] : null,
      gender: typeof member[2] === "string" ? member[2] : null,
    }
  }

  function normalizeInfo<T extends { cast?: unknown; similar?: unknown }>(
    info: T | null,
    people: Map<number, Person>,
  ): T | null {
    if (!info) return info
    let next: T = info
    if (Array.isArray((info as { cast?: unknown }).cast)) {
      const cast = ((info as { cast?: unknown[] }).cast || [])
        .map((member) => normalizeCastMember(member, people))
        .filter((member) => member.name.length > 0)
      next = { ...next, cast } as T
    }
    if (Array.isArray((info as { similar?: unknown }).similar)) {
      const similar = ((info as { similar?: unknown[] }).similar || []).map(normalizeSimilarMember)
      next = { ...next, similar } as T
    }
    return next
  }

  function normalizeMovie(movie: Movie, rootId: string | null, people: Map<number, Person>): Movie {
    return {
      ...movie,
      files: annotateFiles(movie.files, rootId),
      info: normalizeInfo(movie.info, people),
    }
  }

  function normalizeSeries(
    series: Series,
    rootId: string | null,
    people: Map<number, Person>,
  ): Series {
    return {
      ...series,
      seasons: (series.seasons || []).map((season) => ({
        ...season,
        episodes: (season.episodes || []).map((episode) => ({
          ...episode,
          files: annotateFiles(episode.files, rootId),
        })),
      })),
      info: normalizeInfo(series.info, people),
    }
  }

  function mergeMovies(a: MovieUi, b: MovieUi): MovieUi {
    const filesA = annotateFiles(a.files, a.root_id)
    const filesB = annotateFiles(b.files, b.root_id)
    return {
      ...a,
      id: getContentHash(a.id),
      files: mergeTorrentDicts(filesA, filesB),
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
    const filesA = annotateFiles(a.files, rootIdA)
    const filesB = annotateFiles(b.files, rootIdB)
    return {
      ...a,
      files: mergeTorrentDicts(filesA, filesB),
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
          files: annotateFiles(ep.files, rootIdB),
        })
      }
    }
    return {
      ...a,
      episodes: Array.from(episodeMap.values()).sort((a, b) => a.episode_number - b.episode_number),
      poster_path: a.poster_path || b.poster_path,
    }
  }

  function mergeSeries(a: SeriesUi, b: SeriesUi): SeriesUi {
    const seasonMap = new Map<number, Season>()
    for (const season of a.seasons || []) {
      seasonMap.set(season.season_number, {
        ...season,
        episodes: season.episodes.map((ep) => ({
          ...ep,
          files: annotateFiles(ep.files, a.root_id),
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
            files: annotateFiles(ep.files, b.root_id),
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

  function mergeItemsByHash<T extends MovieUi | SeriesUi>(items: T[], mergeFn: (a: T, b: T) => T): T[] {
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
    const movies: MovieUi[] = []
    const series: SeriesUi[] = []
    for (const state of roots.value.values()) {
      movies.push(...state.movieMap.values())
      series.push(...state.seriesMap.values())
    }
    const mergedMovies = mergeItemsByHash(movies, mergeMovies)
    const mergedSeries = mergeItemsByHash(series, mergeSeries)
    return {
      v: 1,
      generated_at: new Date().toISOString(),
      movies: mergedMovies,
      series: mergedSeries,
    }
  }

  function updateMergedState() {
    mediaIndex.value = buildIndex()
    // Consider a root "connected" only after init is received.
    let anyInitialized = false
    for (const state of roots.value.values()) {
      if (state.connected && state.initialized) {
        anyInitialized = true
        break
      }
    }
    if (anyInitialized) {
      loading.value = false
      error.value = null
    }
    connected.value = anyInitialized
  }

  function processJson(state: RootState, text: string) {
    const msg = JSON.parse(text) as WsMessage

    // Prevent out-of-order corruption: buffer delta messages until we receive
    // the initial full-state payload.
    if (msg.type !== "init" && !state.initialized) {
      state.pendingMessages.push(msg)
      return
    }

    switch (msg.type) {
      case "init": {
        state.peopleMap.clear()
        for (const [id, person] of Object.entries(msg.data.people || {})) {
          const parsed = Number(id)
          const normalized = normalizePerson(person)
          if (Number.isFinite(parsed)) {
            state.peopleMap.set(parsed, normalized || { name: "", profile_path: null, gender: null })
          }
        }

        state.movieMap.clear()
        state.seriesMap.clear()
        for (const [id, m] of Object.entries(msg.data.movies || {})) {
          state.movieMap.set(id, withMovieIdentity(id, m, state.rootId, state.peopleMap))
        }
        for (const [id, s] of Object.entries(msg.data.series || {})) {
          state.seriesMap.set(id, withSeriesIdentity(id, s, state.rootId, state.peopleMap))
        }
        state.initialized = true

        // Replay any deltas that arrived before init completed.
        if (state.pendingMessages.length > 0) {
          const queued = state.pendingMessages
          state.pendingMessages = []
          for (const queuedMsg of queued) {
            processJson(state, JSON.stringify(queuedMsg))
          }
        }

        updateMergedState()
        console.log(
          `[WS ${state.rootId}] init: ${state.movieMap.size} movies, ${state.seriesMap.size} series`,
        )
        break
      }
      case "upsert": {
        if (msg.people) {
          for (const [id, person] of Object.entries(msg.people)) {
            const parsed = Number(id)
            const normalized = normalizePerson(person)
            if (Number.isFinite(parsed)) {
              state.peopleMap.set(parsed, normalized || { name: "", profile_path: null, gender: null })
            }
          }
        }

        if (msg.kind === "movie") {
          state.movieMap.set(
            msg.id,
            withMovieIdentity(msg.id, msg.item as Movie, state.rootId, state.peopleMap),
          )
        } else {
          state.seriesMap.set(
            msg.id,
            withSeriesIdentity(msg.id, msg.item as Series, state.rootId, state.peopleMap),
          )
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
        tasks.value.set(info.id, info)
        tasks.value = new Map(tasks.value)
        if (info.status === "completed" || info.status === "cancelled" || info.status === "error") {
          completedTaskIds.add(info.id)
          startTaskSweep()
        }
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
    const url = `${proto}//${location.host}/api/ws/${encodeURIComponent(rootId)}`

    const state: RootState = {
      rootId,
      ws: null,
      movieMap: new Map(),
      seriesMap: new Map(),
      peopleMap: new Map(),
      connected: false,
      initialized: false,
      pendingMessages: [],
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
        state.initialized = false
        state.pendingMessages = []
        updateMergedState()
        console.log(`[WS ${rootId}] Connected`)
      }

      ws.onmessage = (ev) => handleMessage(state, ev)

      ws.onclose = (ev) => {
        state.connected = false
        state.initialized = false
        state.pendingMessages = []
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
    stopTaskSweep()
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
