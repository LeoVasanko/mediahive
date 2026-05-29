import { reactive, watch } from "vue"
import type { Torrent } from "../types"

export type ResolutionPreference = "r2" | "r3" | "r4" | "rmax"
export type HdrPreference = "none" | "hdr10plus" | "dovi"

export interface MediaHiveSettings {
  preferredResolution: ResolutionPreference
  preferredHdr: HdrPreference
  playerId: string | null
  playerCustomCmd: string | null
  playerMpcPort: number | null
}

const STORAGE_KEY = "MediaHive"

const RESOLUTION_PRIORITY: Record<string, number> = {
  "8K": 5,
  "4K": 4,
  "FHD": 3,
  "HD": 2,
  "SD": 1,
}

// Max resolution priority allowed for each preference level
const RESOLUTION_CAP: Record<ResolutionPreference, number> = {
  r2: 2,
  r3: 3,
  r4: 4,
  rmax: 999,
}

const RESOLUTION_VALUES = ["r2", "r3", "r4", "rmax"] as const
const HDR_VALUES = ["none", "hdr10plus", "dovi"] as const

function isResolutionPreference(value: unknown): value is ResolutionPreference {
  return typeof value === "string" && RESOLUTION_VALUES.includes(value as ResolutionPreference)
}

function isHdrPreference(value: unknown): value is HdrPreference {
  return typeof value === "string" && HDR_VALUES.includes(value as HdrPreference)
}

function loadSettings(): MediaHiveSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<MediaHiveSettings>
      return {
        preferredResolution: isResolutionPreference(parsed.preferredResolution)
          ? parsed.preferredResolution
          : "rmax",
        preferredHdr: isHdrPreference(parsed.preferredHdr) ? parsed.preferredHdr : "none",
        playerId: typeof parsed.playerId === "string" ? parsed.playerId : "default",
        playerCustomCmd: typeof parsed.playerCustomCmd === "string" ? parsed.playerCustomCmd : null,
        playerMpcPort: typeof parsed.playerMpcPort === "number" ? parsed.playerMpcPort : null,
      }
    }
  } catch {
    // ignore parse errors
  }
  return { preferredResolution: "rmax", preferredHdr: "none", playerId: "default", playerCustomCmd: null, playerMpcPort: null }
}

const settings = reactive<MediaHiveSettings>(loadSettings())

watch(
  () => ({ ...settings }),
  (value) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
    } catch {
      // ignore
    }
  },
  { deep: true },
)

export function useSettings() {
  return settings
}

/**
 * Sort a list of Torrent objects according to the current format preferences.
 *
 * Algorithm:
 * 1. Compute the "effective priority" = min(actual priority, cap).
 *    Releases exceeding the cap receive the capped priority.
 * 2. Higher effective priority wins.
 * 3. For equal effective priority, prefer HDR flavor per `preferredHdr`.
 * 4. Equal in all above: fall back to raw resolution priority (larger is better)
 *    then size.
 */
export function sortTorrentsByPreference(torrents: Torrent[]): Torrent[] {
  const cap = RESOLUTION_CAP[settings.preferredResolution]

  function effectivePriority(t: Torrent): number {
    const raw = RESOLUTION_PRIORITY[t.resolution ?? ""] ?? 0
    return Math.min(raw, cap)
  }

  type HdrProfile = "sdr" | "hdr10" | "hdr10plus" | "dovi"

  function detectHdrProfile(t: Torrent): HdrProfile {
    const text = [t.title, t.quality, t.codec, t.audio].filter(Boolean).join(" ")
    const hasDovi = t.has_dolby_vision || /dolby\s*vision|dovi|\bdv\b/i.test(text)
    const hasHdr10Plus = /hdr10\+|hdr10plus/i.test(text)
    const hasAnyHdr = t.is_hdr || hasHdr10Plus || hasDovi || /\bhdr\b/i.test(text)

    if (hasDovi) return "dovi"
    if (hasHdr10Plus) return "hdr10plus"
    if (hasAnyHdr) return "hdr10"
    return "sdr"
  }

  function scoreForPreference(profile: HdrProfile): number {
    if (settings.preferredHdr === "none") {
      // Prefer no HDR; HDR variants after SDR.
      if (profile === "sdr") return 4
      if (profile === "hdr10") return 3
      if (profile === "hdr10plus") return 2
      return 1
    }

    if (settings.preferredHdr === "hdr10plus") {
      // Requested behavior: HDR10+ best, HDR10 next, SDR then, DoVi last.
      if (profile === "hdr10plus") return 4
      if (profile === "hdr10") return 3
      if (profile === "sdr") return 2
      return 1
    }

    if (profile === "dovi") return 4
    if (profile === "hdr10plus") return 3
    if (profile === "hdr10") return 2
    return 1
  }

  function hdrPreferenceScore(t: Torrent): number {
    return scoreForPreference(detectHdrProfile(t))
  }

  return [...torrents].sort((a, b) => {
    const epA = effectivePriority(a)
    const epB = effectivePriority(b)
    if (epB !== epA) return epB - epA

    // Same effective resolution bucket - apply HDR format preference
    const hdrScoreA = hdrPreferenceScore(a)
    const hdrScoreB = hdrPreferenceScore(b)
    if (hdrScoreB !== hdrScoreA) {
      return hdrScoreB - hdrScoreA
    }

    // Fall back to raw resolution priority then size
    const rawA = RESOLUTION_PRIORITY[a.resolution ?? ""] ?? 0
    const rawB = RESOLUTION_PRIORITY[b.resolution ?? ""] ?? 0
    if (rawB !== rawA) return rawB - rawA

    return (b.size ?? 0) - (a.size ?? 0)
  })
}
