import * as flagSvgs from "country-flag-icons/string/3x2"

export interface LanguageFlagEntry {
  countryCode: string
  svg: string
  sourceCodes: string[]
}

const FLAGS = flagSvgs as Record<string, string>

const LANGUAGE_TO_COUNTRY: Record<string, string> = {
  // English
  en: "GB",
  eng: "GB",

  // Spanish (including LATAM variants collapsed to Spain flag)
  es: "ES",
  spa: "ES",
  esl: "ES",
  spl: "ES",
  "es-es": "ES",
  "es-419": "ES",
  "spa-la": "ES",

  // Portuguese
  pt: "PT",
  por: "PT",
  "pt-pt": "PT",
  "pt-br": "BR",

  // Major European languages
  fr: "FR",
  fra: "FR",
  fre: "FR",
  de: "DE",
  deu: "DE",
  ger: "DE",
  it: "IT",
  ita: "IT",
  nl: "NL",
  nld: "NL",
  dut: "NL",
  sv: "SE",
  swe: "SE",
  no: "NO",
  nor: "NO",
  da: "DK",
  dan: "DK",
  fi: "FI",
  fin: "FI",
  pl: "PL",
  पोल: "PL",
  cs: "CZ",
  ces: "CZ",
  cze: "CZ",
  hu: "HU",
  hun: "HU",
  ro: "RO",
  ron: "RO",
  rum: "RO",
  el: "GR",
  gre: "GR",
  ell: "GR",
  tr: "TR",
  tur: "TR",

  // Slavic / Eurasian
  ru: "RU",
  rus: "RU",
  uk: "UA",
  ukr: "UA",
  bg: "BG",
  bul: "BG",
  sr: "RS",
  srp: "RS",
  hr: "HR",
  hrv: "HR",
  sl: "SI",
  slv: "SI",
  sk: "SK",
  slk: "SK",
  slo: "SK",

  // East / South / SE Asia
  ja: "JP",
  jpn: "JP",
  ko: "KR",
  kor: "KR",
  zh: "CN",
  zho: "CN",
  chi: "CN",
  yue: "HK",
  th: "TH",
  tha: "TH",
  vi: "VN",
  vie: "VN",
  id: "ID",
  ind: "ID",
  ms: "MY",
  msa: "MY",
  may: "MY",
  hi: "IN",
  hin: "IN",

  // Middle East / Africa
  ar: "SA",
  ara: "SA",
  he: "IL",
  heb: "IL",
  fa: "IR",
  fas: "IR",
  per: "IR",
  ur: "PK",
  urd: "PK",
  sw: "TZ",
  swa: "TZ",

  // Other common
  ca: "ES",
  cat: "ES",
  eu: "ES",
  baq: "ES",
  eus: "ES",
}

function normalizeLanguageCode(code: string): string {
  return code.trim().toLowerCase().replace("_", "-")
}

function normalizeLanguageName(name: string): string {
  return name.trim().toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ")
}

let _browserLanguagePreferences: string[] | null = null
let _browserPreferenceRanks: Map<string, number> | null = null

const LANGUAGE_NAME_TO_CODE: Record<string, string> = {
  english: "en",
  spanish: "es",
  portuguese: "pt",
  french: "fr",
  german: "de",
  italian: "it",
  dutch: "nl",
  swedish: "sv",
  norwegian: "no",
  danish: "da",
  finnish: "fi",
  polish: "pl",
  czech: "cs",
  hungarian: "hu",
  romanian: "ro",
  greek: "el",
  turkish: "tr",
  russian: "ru",
  ukrainian: "uk",
  bulgarian: "bg",
  serbian: "sr",
  croatian: "hr",
  slovenian: "sl",
  slovak: "sk",
  japanese: "ja",
  korean: "ko",
  chinese: "zh",
  cantonese: "yue",
  thai: "th",
  vietnamese: "vi",
  indonesian: "id",
  malay: "ms",
  hindi: "hi",
  arabic: "ar",
  hebrew: "he",
  persian: "fa",
  urdu: "ur",
  swahili: "sw",
  catalan: "ca",
  basque: "eu",
}

function getBrowserLanguagePreferences(): string[] {
  if (_browserLanguagePreferences) return _browserLanguagePreferences

  const preferences: string[] = []
  if (typeof navigator !== "undefined") {
    if (Array.isArray(navigator.languages)) {
      for (const lang of navigator.languages) {
        if (typeof lang === "string" && lang.trim()) {
          preferences.push(normalizeLanguageCode(lang))
        }
      }
    }
    if (typeof navigator.language === "string" && navigator.language.trim()) {
      preferences.push(normalizeLanguageCode(navigator.language))
    }
  }

  const deduped = Array.from(new Set(preferences))
  _browserLanguagePreferences = deduped
  return deduped
}

function getBrowserPreferenceRanks(): Map<string, number> {
  if (_browserPreferenceRanks) return _browserPreferenceRanks

  const preferences = getBrowserLanguagePreferences()
  const ranks = new Map<string, number>()
  const display = new Intl.DisplayNames(["en"], { type: "language" })

  for (const [index, pref] of preferences.entries()) {
    const base = pref.split("-", 1)[0]
    if (!ranks.has(pref)) ranks.set(pref, index)
    if (!ranks.has(base)) ranks.set(base, index)

    const prefName = display.of(pref)
    if (prefName) {
      const key = normalizeLanguageName(prefName)
      if (!ranks.has(key)) ranks.set(key, index)
    }
    const baseName = display.of(base)
    if (baseName) {
      const key = normalizeLanguageName(baseName)
      if (!ranks.has(key)) ranks.set(key, index)
    }
  }

  _browserPreferenceRanks = ranks
  return ranks
}

function resolveLanguageIdentifier(value: string): string {
  const normalized = normalizeLanguageCode(value)
  if (/^[a-z]{2,3}(?:-[a-z0-9]{2,})?$/i.test(normalized)) {
    return normalized
  }

  const nameKey = normalizeLanguageName(value)
  const byName = LANGUAGE_NAME_TO_CODE[nameKey]
  if (byName) return byName

  return normalized
}

function getPreferenceRank(value: string, preferenceRanks: Map<string, number>): number {
  const normalized = resolveLanguageIdentifier(value)
  const exact = preferenceRanks.get(normalized)
  if (exact !== undefined) return exact

  const base = normalized.split("-", 1)[0]
  const baseRank = preferenceRanks.get(base)
  if (baseRank !== undefined) return baseRank

  const nameKey = normalizeLanguageName(toLanguageName(normalized))
  const nameRank = preferenceRanks.get(nameKey)
  if (nameRank !== undefined) return nameRank

  const rawNameRank = preferenceRanks.get(normalizeLanguageName(value))
  if (rawNameRank !== undefined) return rawNameRank

  return Number.MAX_SAFE_INTEGER
}

export function mapLanguageToCountry(code: string): string | null {
  const normalized = resolveLanguageIdentifier(code)

  const direct = LANGUAGE_TO_COUNTRY[normalized]
  if (direct) return direct

  // region-tag style code like en-us / pt-br / es-mx
  const hyphenParts = normalized.split("-")
  if (hyphenParts.length >= 2) {
    const region = hyphenParts[hyphenParts.length - 1]
    if (/^[a-z]{2}$/i.test(region)) {
      return region.toUpperCase()
    }
  }

  return null
}

export function buildLanguageFlags(codes: string[] | null | undefined): {
  flags: LanguageFlagEntry[]
  unmappedCodes: string[]
} {
  if (!codes || codes.length === 0) {
    return { flags: [], unmappedCodes: [] }
  }

  const byCountry = new Map<string, string[]>()
  const unmapped: string[] = []
  const seenUnmapped = new Set<string>()
  const preferenceRanks = getBrowserPreferenceRanks()

  const ordered = codes
    .map((raw, index) => ({ raw, index }))
    .filter((v) => Boolean(v.raw && String(v.raw).trim()))
    .sort((a, b) => {
      const aRank = getPreferenceRank(String(a.raw), preferenceRanks)
      const bRank = getPreferenceRank(String(b.raw), preferenceRanks)
      if (aRank !== bRank) return aRank - bRank
      return a.index - b.index
    })

  for (const { raw } of ordered) {
    if (!raw) continue
    const countryCode = mapLanguageToCountry(raw)
    if (!countryCode) {
      const upper = raw.toUpperCase()
      if (!seenUnmapped.has(upper)) {
        seenUnmapped.add(upper)
        unmapped.push(upper)
      }
      continue
    }
    if (!FLAGS[countryCode]) {
      const upper = raw.toUpperCase()
      if (!seenUnmapped.has(upper)) {
        seenUnmapped.add(upper)
        unmapped.push(upper)
      }
      continue
    }
    const existing = byCountry.get(countryCode) || []
    existing.push(raw)
    byCountry.set(countryCode, existing)
  }

  const flags: LanguageFlagEntry[] = Array.from(byCountry.entries()).map(
    ([countryCode, sourceCodes]) => ({
      countryCode,
      svg: FLAGS[countryCode],
      sourceCodes,
    }),
  )

  return {
    flags,
    unmappedCodes: unmapped,
  }
}

const LANGUAGE_NAME_OVERRIDES: Record<string, string> = {
  eng: "English",
  spa: "Spanish",
  "spa-la": "Spanish",
  esl: "Spanish",
  spl: "Spanish",
  por: "Portuguese",
  fre: "French",
  fra: "French",
  ger: "German",
  deu: "German",
  ita: "Italian",
  nld: "Dutch",
  dut: "Dutch",
  swe: "Swedish",
  nor: "Norwegian",
  dan: "Danish",
  fin: "Finnish",
  pol: "Polish",
  ces: "Czech",
  cze: "Czech",
  hun: "Hungarian",
  ron: "Romanian",
  rum: "Romanian",
  ell: "Greek",
  gre: "Greek",
  tur: "Turkish",
  rus: "Russian",
  ukr: "Ukrainian",
  bul: "Bulgarian",
  srp: "Serbian",
  hrv: "Croatian",
  slv: "Slovenian",
  slk: "Slovak",
  slo: "Slovak",
  jpn: "Japanese",
  kor: "Korean",
  zho: "Chinese",
  chi: "Chinese",
  yue: "Cantonese",
  tha: "Thai",
  vie: "Vietnamese",
  ind: "Indonesian",
  msa: "Malay",
  may: "Malay",
  hin: "Hindi",
  ara: "Arabic",
  heb: "Hebrew",
  fas: "Persian",
  per: "Persian",
  urd: "Urdu",
  swa: "Swahili",
  cat: "Catalan",
  eus: "Basque",
  baq: "Basque",
}

function toLanguageName(code: string): string {
  const normalized = normalizeLanguageCode(code)
  const override = LANGUAGE_NAME_OVERRIDES[normalized]
  if (override) return override

  const display = new Intl.DisplayNames(["en"], { type: "language" })
  const candidate = display.of(normalized)
  if (candidate) return candidate

  const base = normalized.split("-", 1)[0]
  const baseOverride = LANGUAGE_NAME_OVERRIDES[base]
  if (baseOverride) return baseOverride
  const baseCandidate = display.of(base)
  if (baseCandidate) return baseCandidate

  return code.toUpperCase()
}

function summarizeLanguageCodes(codes: string[] | null | undefined): string {
  if (!codes || codes.length === 0) return ""
  const names: string[] = []
  const seen = new Set<string>()

  for (const raw of codes) {
    if (!raw) continue
    const name = toLanguageName(raw).trim()
    if (!name) continue
    const key = name.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    names.push(name)
  }

  return names.join(", ")
}

export function formatAudioSubtitleSummary(
  audioCodes: string[] | null | undefined,
  subtitleCodes: string[] | null | undefined,
): string {
  const audio = summarizeLanguageCodes(audioCodes)
  const subs = summarizeLanguageCodes(subtitleCodes)
  if (audio && subs) return `${audio} / ${subs}`
  return audio || subs
}
