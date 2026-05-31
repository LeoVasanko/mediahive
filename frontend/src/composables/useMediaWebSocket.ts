import { shallowRef, readonly, onUnmounted } from "vue"
import type {
  CastGender,
  CastMember,
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
  WsRootStatus,
} from "../types"

interface RootState {
  movieMap: Map<string, MovieUi>
  seriesMap: Map<string, SeriesUi>
  peopleMap: Map<number, Person>
  initialized: boolean
  pendingMessages: WsMessage[]
}

export interface RootStatusEntry extends WsRootStatus {}

const MERGED_KEY_DELIMITER = "::"

/**
 * Composable that connects to one all-roots MediaHive WebSocket and keeps
 * a merged media index updated in real time.
 */
export function useMediaWebSocket() {
  type RootTaskInfo = TaskInfo & { root_id: string }

  const mediaIndex = shallowRef<MediaIndex | null>(null)
  const loading = shallowRef(true)
  const error = shallowRef<string | null>(null)
  const connected = shallowRef(false)
  const tasks = shallowRef<Map<string, RootTaskInfo>>(new Map())
  const roots = shallowRef<Map<string, RootStatusEntry>>(new Map())

  const rootStates = shallowRef<Map<string, RootState>>(new Map())
  const wsRef = shallowRef<WebSocket | null>(null)
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
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
  ): CastMember {
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
      gender: person?.gender ?? null,
      id,
    }
  }

  function normalizePerson(member: unknown): Person | null {
    if (!Array.isArray(member)) return null
    const gender = normalizeCastGender(member[2])
    return {
      name: typeof member[0] === "string" ? member[0] : "",
      profile_path: typeof member[1] === "string" ? member[1] : null,
      gender,
    }
  }

  function normalizeCastGender(value: unknown): CastGender | null {
    if (typeof value !== "string") return null
    switch (value) {
      case "female":
      case "male":
      case "non_binary":
      case "unknown":
        return value
      default:
        return null
    }
  }

  function normalizeInfo<T extends { cast?: unknown }>(
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

  function ensureRootState(rootId: string): RootState {
    const existing = rootStates.value.get(rootId)
    if (existing) {
      return existing
    }
    const created: RootState = {
      movieMap: new Map(),
      seriesMap: new Map(),
      peopleMap: new Map(),
      initialized: false,
      pendingMessages: [],
    }
    rootStates.value.set(rootId, created)
    return created
  }

  function pruneMissingRoots(nextRoots: Map<string, RootStatusEntry>) {
    for (const rootId of rootStates.value.keys()) {
      if (!nextRoots.has(rootId)) {
        rootStates.value.delete(rootId)
      }
    }

    for (const [taskKey, task] of tasks.value.entries()) {
      if (!nextRoots.has(task.root_id)) {
        tasks.value.delete(taskKey)
      }
    }
    tasks.value = new Map(tasks.value)
  }

  function buildIndex(): MediaIndex {
    const movies: MovieUi[] = []
    const series: SeriesUi[] = []
    for (const state of rootStates.value.values()) {
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

    let anyInitialized = false
    for (const state of rootStates.value.values()) {
      if (state.initialized) {
        anyInitialized = true
        break
      }
    }

    if (anyInitialized || roots.value.size === 0) {
      loading.value = false
      error.value = null
    }

    connected.value = wsRef.value?.readyState === WebSocket.OPEN
  }

  function applyRootInit(rootId: string, rootData: { movies: Record<string, Movie>; series: Record<string, Series>; people?: Record<string, unknown> }) {
    const state = ensureRootState(rootId)

    state.peopleMap.clear()
    for (const [id, person] of Object.entries(rootData.people || {})) {
      const parsed = Number(id)
      const normalized = normalizePerson(person)
      if (Number.isFinite(parsed)) {
        state.peopleMap.set(parsed, normalized || { name: "", profile_path: null, gender: null })
      }
    }

    state.movieMap.clear()
    state.seriesMap.clear()
    for (const [id, m] of Object.entries(rootData.movies || {})) {
      state.movieMap.set(id, withMovieIdentity(id, m, rootId, state.peopleMap))
    }
    for (const [id, s] of Object.entries(rootData.series || {})) {
      state.seriesMap.set(id, withSeriesIdentity(id, s, rootId, state.peopleMap))
    }

    state.initialized = true

    if (state.pendingMessages.length > 0) {
      const queued = state.pendingMessages
      state.pendingMessages = []
      for (const queuedMsg of queued) {
        processMessage(queuedMsg)
      }
    }
  }

  function processMessage(msg: WsMessage) {
    switch (msg.type) {
      case "roots": {
        const next = new Map<string, RootStatusEntry>()
        for (const root of msg.roots || []) {
          next.set(root.root_id, { ...root })
          ensureRootState(root.root_id)
        }
        roots.value = next
        pruneMissingRoots(next)
        updateMergedState()
        return
      }

      case "init": {
        for (const [rootId, rootData] of Object.entries(msg.roots || {})) {
          applyRootInit(rootId, rootData)
        }
        updateMergedState()
        return
      }

      case "upsert": {
        const state = ensureRootState(msg.root_id)
        if (!state.initialized) {
          state.pendingMessages.push(msg)
          return
        }

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
            withMovieIdentity(msg.id, msg.item as Movie, msg.root_id, state.peopleMap),
          )
        } else {
          state.seriesMap.set(
            msg.id,
            withSeriesIdentity(msg.id, msg.item as Series, msg.root_id, state.peopleMap),
          )
        }
        updateMergedState()
        return
      }

      case "remove": {
        const state = ensureRootState(msg.root_id)
        if (!state.initialized) {
          state.pendingMessages.push(msg)
          return
        }

        if (msg.kind === "movie") {
          state.movieMap.delete(msg.id)
        } else {
          state.seriesMap.delete(msg.id)
        }
        updateMergedState()
        return
      }

      case "task": {
        const info = msg.data
        const taskKey = `${msg.root_id}:${info.id}`
        tasks.value.set(taskKey, { ...info, root_id: msg.root_id })
        tasks.value = new Map(tasks.value)
        if (info.status === "completed" || info.status === "cancelled" || info.status === "error") {
          completedTaskIds.add(taskKey)
          startTaskSweep()
        }
        return
      }
    }
  }

  function handleRawMessage(event: MessageEvent) {
    const processText = (text: string) => {
      try {
        processMessage(JSON.parse(text) as WsMessage)
      } catch (e) {
        console.error("[WS] Failed to handle message:", e)
      }
    }

    if (event.data instanceof Blob) {
      void event.data.text().then(processText)
      return
    }
    if (event.data instanceof ArrayBuffer) {
      processText(new TextDecoder().decode(event.data))
      return
    }
    processText(event.data as string)
  }

  function scheduleReconnect() {
    if (disposed) return
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, 2000)
  }

  function connect() {
    if (disposed) return
    if (wsRef.value && wsRef.value.readyState <= WebSocket.OPEN) return

    const proto = location.protocol === "https:" ? "wss:" : "ws:"
    const url = `${proto}//${location.host}/api/ws`

    console.log(`[WS] Connecting to ${url}...`)
    const ws = new WebSocket(url)
    wsRef.value = ws

    ws.onopen = () => {
      connected.value = true
      error.value = null
      console.log("[WS] Connected")
    }

    ws.onmessage = (ev) => handleRawMessage(ev)

    ws.onclose = (ev) => {
      if (wsRef.value === ws) {
        wsRef.value = null
      }
      connected.value = false
      console.log(`[WS] Closed (code=${ev.code})`)
      scheduleReconnect()
    }

    ws.onerror = (ev) => {
      console.error("[WS] Error:", ev)
      if (!mediaIndex.value) {
        error.value = "WebSocket connection failed"
      }
    }
  }

  function disconnect() {
    disposed = true
    stopTaskSweep()

    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }

    if (wsRef.value) {
      wsRef.value.onclose = null
      wsRef.value.close()
      wsRef.value = null
    }

    connected.value = false
    roots.value.clear()
    rootStates.value.clear()
  }

  connect()
  onUnmounted(disconnect)

  return {
    mediaIndex,
    loading: readonly(loading),
    error: readonly(error),
    connected: readonly(connected),
    tasks: readonly(tasks),
    roots: readonly(roots),
    disconnect,
  }
}
