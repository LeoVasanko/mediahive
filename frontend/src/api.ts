export interface PlayerStatus {
  remote: boolean
}

export interface PlayerInfo {
  id: string
  name: string
  family: string
  path: string | null
}

export interface RootStatus {
  root_id: string
  name: string
  path: string
  status: string
  error: string | null
  movies: number
  series: number
}

export interface RootsResponse {
  roots: RootStatus[]
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

/**
 * Fetch active roots and their statuses
 */
export async function fetchRoots(): Promise<RootStatus[]> {
  const response = await fetch("/api/roots")
  if (!response.ok) {
    throw new Error(`Failed to load roots: ${response.statusText}`)
  }
  const data = await response.json()
  return data.roots || []
}

/**
 * Fetch merged resume positions from all roots.
 */
export async function fetchResumePositions(): Promise<Record<string, number>> {
  try {
    const roots = await fetchRoots()
    const merged: Record<string, number> = {}
    await Promise.all(
      roots.map(async (root) => {
        const response = await fetch(
          `/api/roots/${encodeURIComponent(root.root_id)}/playback/resume-positions`,
        )
        if (!response.ok) return
        const data = await response.json().catch(() => ({}))
        const positions = data?.resume_positions
        if (positions && typeof positions === "object") {
          Object.assign(merged, positions)
        }
      }),
    )
    return merged
  } catch {
    return {}
  }
}

/**
 * Replace the full root set atomically
 */
export async function replaceRoots(
  roots: Record<string, string>,
): Promise<{ accepted: RootStatus[]; failed: unknown[] }> {
  const response = await fetch("/api/roots", {
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
): Promise<void> {
  const normalizedPath = normalizeMediaPath(filePath)
  const body: Record<string, unknown> = { file_path: normalizedPath }
  if (playerId) body.player_id = playerId
  if (playerCustomCmd) body.player_custom_cmd = playerCustomCmd
  try {
    const response = await fetch(`/api/roots/${encodeURIComponent(rootId)}/play`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || response.statusText)
    }
  } catch (e) {
    console.error("Play media error:", e)
    alert(`Failed to play media.\n\n${e}`)
  }
}

/**
 * Open a folder in the system file manager
 */
export async function openFolder(rootId: string, folderPath: string): Promise<void> {
  const normalizedPath = normalizeMediaPath(folderPath)
  try {
    const response = await fetch(`/api/roots/${encodeURIComponent(rootId)}/open-folder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder_path: normalizedPath }),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || response.statusText)
    }
  } catch (e) {
    console.error("Open folder error:", e)
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

  // Convert relative path to URL path for FastAPI server
  // .mediahive/covers/Movies/... -> /api/media/{root_id}/.mediahive/covers/Movies/...
  let urlPath = coverPath

  // Remove drive letter (Z:) and convert backslashes to forward slashes
  if (urlPath.match(/^[A-Za-z]:/)) {
    urlPath = urlPath.substring(2)
  }
  urlPath = urlPath.replace(/\\/g, "/")

  // Ensure path starts with /
  if (!urlPath.startsWith("/")) {
    urlPath = "/" + urlPath
  }

  // Encode URI components but preserve slashes
  const encodedPath = urlPath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/")

  const rid = rootId || "unknown"
  return `/api/media/${encodeURIComponent(rid)}${encodedPath}`
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
