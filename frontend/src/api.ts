export interface PlayerStatus {
  remote: boolean
}

export interface PlayerInfo {
  id: string
  name: string
  family: string
  path: string | null
}

export interface RootEntry {
  root_id: string
  path: string
}

interface ActionTimingContext {
  actionStartedAt?: number
  source?: string
}

function nowMs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return performance.now()
  }
  return Date.now()
}

function makeTraceId(action: string): string {
  const suffix = Math.random().toString(16).slice(2, 8)
  return `${action}-${Date.now().toString(36)}-${suffix}`
}

function logActionTiming(
  action: string,
  traceId: string,
  status: number,
  actionToFetchMs: number,
  fetchMs: number,
  totalMs: number,
  serverTiming: string | null,
  source?: string,
) {
  const sourceTag = source ? ` source=${source}` : ""
  const serverTag = serverTiming ? ` serverTiming=${serverTiming}` : ""
  console.info(
    `[timing:${action}] trace=${traceId}${sourceTag} status=${status} actionToFetch=${actionToFetchMs.toFixed(1)}ms fetch=${fetchMs.toFixed(1)}ms total=${totalMs.toFixed(1)}ms${serverTag}`,
  )
}

export function normalizeMediaPath(input: string): string {
  return input
    .replace(/\\/g, "/")
    .replace(/^[A-Za-z]:\//, "")
    .replace(/^\/+/, "")
}

export function isVideoPath(path: string | null | undefined): boolean {
  return Boolean(path && /\.(webm|mp4|mkv|avi|mov)$/i.test(path))
}

export interface VideoSourceAttributes {
  type: string
  codecs: string
}

export function isSafariBrowser(): boolean {
  if (typeof navigator === "undefined") {
    return false
  }

  const ua = navigator.userAgent
  return /Safari/i.test(ua) && !/Chrome|Chromium|CriOS|Edg|OPR|FxiOS/i.test(ua)
}

export function getVideoPreviewUrl(url: string): string {
  if (!url || !isSafariBrowser()) {
    return url
  }

  if (url.includes("#")) {
    return url
  }

  // Safari often needs a tiny time offset to paint the first frame before playback.
  return `${url}#t=0.001`
}

export function getVideoSourceAttributes(path: string | null | undefined): VideoSourceAttributes {
  if (!path) {
    return { type: "video/mp4", codecs: "hvc1" }
  }

  if (/\.webm$/i.test(path)) {
    return { type: "video/webm", codecs: "av1" }
  }

  if (/\.mp4$/i.test(path) || /\.m4v$/i.test(path)) {
    return { type: "video/mp4", codecs: "hvc1" }
  }

  if (/\.mov$/i.test(path)) {
    return { type: "video/quicktime", codecs: "hvc1" }
  }

  if (/\.avi$/i.test(path)) {
    return { type: "video/x-msvideo", codecs: "" }
  }

  if (/\.mkv$/i.test(path)) {
    return { type: "video/x-matroska", codecs: "" }
  }

  return { type: "video/mp4", codecs: "hvc1" }
}

function encodePathSegments(path: string): string {
  return path
    .split("/")
    .filter((segment) => segment.length > 0)
    .map((segment) => encodeURIComponent(segment))
    .join("/")
}

function normalizeCoverPath(path: string): string {
  return path
    .replace(/\\/g, "/")
    .replace(/^[A-Za-z]:\//, "")
    .replace(/^\/+/, "")
}

function toRootAssetPath(path: string): string | null {
  const normalized = normalizeCoverPath(path)
  const marker = ".mediahive/"
  const idx = normalized.toLowerCase().indexOf(marker)
  if (idx < 0) return null
  const logical = normalized.slice(idx + marker.length)
  return logical.length > 0 ? logical : null
}

function splitAssetTypePath(assetPath: string): { assetType: string; relativePath: string } | null {
  const parts = assetPath.split("/").filter((segment) => segment.length > 0)
  if (parts.length < 2) return null
  const [assetType, ...rest] = parts
  if (!assetType || !["movies", "series", "people"].includes(assetType.toLowerCase())) {
    return null
  }
  return { assetType: assetType.toLowerCase(), relativePath: rest.join("/") }
}

/**
 * Fetch merged resume positions from all roots.
 */
export async function fetchResumePositions(): Promise<Record<string, number>> {
  try {
    const response = await fetch("/api/meta/playback-state")
    if (!response.ok) return {}
    const data = await response.json().catch(() => ({}))
    const positions = data?.data?.resume_positions
    if (!positions || typeof positions !== "object") {
      return {}
    }
    const normalized: Record<string, number> = {}
    for (const [slug, value] of Object.entries(positions as Record<string, unknown>)) {
      if (!value || typeof value !== "object") continue
      const pos = (value as { pos?: unknown }).pos
      if (typeof pos === "number" && Number.isFinite(pos) && pos > 0) {
        normalized[slug] = pos
      }
    }
    return normalized
  } catch {
    return {}
  }
}

/**
 * Replace the full root set atomically
 */
export async function replaceRoots(
  roots: Record<string, string>,
): Promise<{ accepted: RootEntry[]; failed: unknown[] }> {
  const response = await fetch("/api/config/roots", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ roots }),
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(err.detail || response.statusText)
  }
  return response.json()
}

/**
 * Fetch detected media players from the backend.
 */
export async function fetchPlayers(): Promise<PlayerInfo[]> {
  const response = await fetch("/api/players")
  if (!response.ok) {
    throw new Error(`Failed to load players: ${response.statusText}`)
  }
  const data = await response.json()
  return data.players || []
}

/**
 * Play a media file with the selected player.
 */
export async function playMedia(
  rootId: string,
  filePath: string,
  playerId?: string | null,
  playerCustomCmd?: string | null,
  timing?: ActionTimingContext,
): Promise<void> {
  const normalizedPath = normalizeMediaPath(filePath)
  const body: Record<string, unknown> = { file_path: normalizedPath }
  if (playerId) body.player_id = playerId
  if (playerCustomCmd) body.player_custom_cmd = playerCustomCmd
  const actionStart = timing?.actionStartedAt ?? nowMs()
  const traceId = makeTraceId("play")
  try {
    const fetchStart = nowMs()
    const actionToFetchMs = Math.max(0, fetchStart - actionStart)
    const clientSentMs = Date.now()
    const actionStartEpochMs = clientSentMs - actionToFetchMs
    const response = await fetch(`/api/play/${encodeURIComponent(rootId)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-MediaHive-Trace-Id": traceId,
        "X-MediaHive-Client-Sent-Ms": clientSentMs.toFixed(3),
        "X-MediaHive-Client-Action-Start-Ms": actionStartEpochMs.toFixed(3),
      },
      body: JSON.stringify(body),
    })
    const fetchMs = Math.max(0, nowMs() - fetchStart)
    const totalMs = Math.max(0, nowMs() - actionStart)
    const serverTiming = response.headers.get("server-timing")
    const responseTraceId = response.headers.get("x-mediahive-trace-id") || traceId
    logActionTiming(
      "play",
      responseTraceId,
      response.status,
      actionToFetchMs,
      fetchMs,
      totalMs,
      serverTiming,
      timing?.source,
    )
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || response.statusText)
    }
  } catch (e) {
    const totalMs = Math.max(0, nowMs() - actionStart)
    console.error(`Play media error after ${totalMs.toFixed(1)}ms (trace=${traceId}):`, e)
    alert(`Failed to play media.\n\n${e}`)
  }
}

/**
 * Open a folder in the system file manager
 */
export async function openFolder(
  rootId: string,
  folderPath: string,
  timing?: ActionTimingContext,
): Promise<void> {
  const normalizedPath = normalizeMediaPath(folderPath)
  const actionStart = timing?.actionStartedAt ?? nowMs()
  const traceId = makeTraceId("open-folder")
  try {
    const fetchStart = nowMs()
    const actionToFetchMs = Math.max(0, fetchStart - actionStart)
    const clientSentMs = Date.now()
    const actionStartEpochMs = clientSentMs - actionToFetchMs
    const response = await fetch(`/api/open-folder/${encodeURIComponent(rootId)}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-MediaHive-Trace-Id": traceId,
        "X-MediaHive-Client-Sent-Ms": clientSentMs.toFixed(3),
        "X-MediaHive-Client-Action-Start-Ms": actionStartEpochMs.toFixed(3),
      },
      body: JSON.stringify({ folder_path: normalizedPath }),
    })
    const fetchMs = Math.max(0, nowMs() - fetchStart)
    const totalMs = Math.max(0, nowMs() - actionStart)
    const serverTiming = response.headers.get("server-timing")
    const responseTraceId = response.headers.get("x-mediahive-trace-id") || traceId
    logActionTiming(
      "open-folder",
      responseTraceId,
      response.status,
      actionToFetchMs,
      fetchMs,
      totalMs,
      serverTiming,
      timing?.source,
    )
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || response.statusText)
    }
  } catch (e) {
    const totalMs = Math.max(0, nowMs() - actionStart)
    console.error(`Open folder error after ${totalMs.toFixed(1)}ms (trace=${traceId}):`, e)
    alert(`Failed to open folder.\n\n${e}`)
  }
}

/**
 * Return whether MPC-BE local web control is currently reachable.
 * @param port - Optional custom port (default 13579)
 */
export async function isMpcBeReachable(port?: number | null): Promise<boolean> {
  try {
    const url = port ? `/api/mpcbe/status?port=${port}` : "/api/mpcbe/status"
    const response = await fetch(url)
    if (!response.ok) return false
    const data = await response.json().catch(() => ({}))
    return Boolean(data.reachable)
  } catch {
    return false
  }
}

/**
 * Return player integration capabilities for the current OS.
 * @param port - Optional custom MPC-BE port (default 13579)
 */
export async function getPlayerStatus(port?: number | null): Promise<PlayerStatus> {
  const url = port ? `/api/player/status?port=${port}` : "/api/player/status"
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Failed to load player status: ${response.statusText}`)
  }
  return response.json()
}

/**
 * Convert a cover path to a displayable URL.
 * Uses FastAPI server for async file serving.
 *
 * The path comes from the server already converted to Windows format (Z:\...)
 * Paths starting with '/' are TMDB relative paths that weren't fetched - ignore them
 */
export function getCoverUrl(coverPath: string | null, rootId?: string | null): string {
  if (!coverPath) {
    return ""
  }
  // Ignore TMDB relative paths (start with /) - these are bugs in the index
  if (coverPath.startsWith("/")) {
    return ""
  }

  const rid = rootId || "unknown"
  const assetPath = toRootAssetPath(coverPath)
  if (assetPath) {
    const split = splitAssetTypePath(assetPath)
    if (split) {
      return `/api/assets/${encodeURIComponent(rid)}/${encodeURIComponent(split.assetType)}/${encodePathSegments(split.relativePath)}`
    }
  }

  const mediaPath = normalizeCoverPath(coverPath)
  return `/api/media/${encodeURIComponent(rid)}/${encodePathSegments(mediaPath)}`
}

/**
 * Invoke the native OS folder picker via pywebview, then add the selected
 * folder to the server's root list. Only works inside the packaged desktop app.
 */
export async function pickFolderAndAddRoot(): Promise<string | null> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const api = (window as any).pywebview?.api
  if (!api) return null
  const folder: string | null = await api.pick_folder()
  return folder
}
