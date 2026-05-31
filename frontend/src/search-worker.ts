// Search Web Worker - runs search off the main thread
// This file is loaded as a Web Worker, not imported as a module.

import type {
  MovieUi,
  SeriesUi,
  MatchedPerson,
  MatchedEpisode,
  SearchMatchInfo,
} from "./types"

// ---------------------------------------------------------------------------
// Message types
// ---------------------------------------------------------------------------

export interface SearchIndexMessage {
  type: "index"
  movies: MovieUi[]
  series: SeriesUi[]
}

export interface SearchQueryMessage {
  type: "query"
  id: number
  query: string
}

export type SearchWorkerMessage = SearchIndexMessage | SearchQueryMessage

export interface SearchResultItem {
  id: string
  title: string | null
  year?: number | null
  cover_path: string | null
  showreel_images?: string[] | null
  showreel_source_sets?: string[][] | null
  type: "movies" | "series"
  resolution?: string | null
  root_id: string | null
  searchMatchInfo?: SearchMatchInfo
}

export interface SearchCategoryResult {
  name: string
  items: SearchResultItem[]
}

export interface SearchResponseMessage {
  id: number
  results: SearchResultItem[]
  categories: SearchCategoryResult[]
}

// ---------------------------------------------------------------------------
// Worker state
// ---------------------------------------------------------------------------

let movies: MovieUi[] = []
let series: SeriesUi[] = []

// ---------------------------------------------------------------------------
// Normalization helpers (mirrored from App.vue)
// ---------------------------------------------------------------------------

function normalizeSearchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\-]+/g, " ")
    .trim()
    .replace(/\s+/g, " ")
}

function normalizePathSearchText(value: string): string {
  return value
    .toLowerCase()
    .replace(/[\\/]+/g, "/")
    .replace(/[^a-z0-9/]+/g, " ")
    .trim()
    .replace(/\s+/g, " ")
}

// ---------------------------------------------------------------------------
// Scoring helpers (mirrored from App.vue)
// ---------------------------------------------------------------------------

function getTermMatchScore(term: string, field: string): number {
  const index = field.indexOf(term)
  if (index < 0) return 0
  if (index === 0) return 100
  const charBefore = field[index - 1]
  if (/\s/.test(charBefore)) return 80
  if (index < field.length / 2) return 50
  return 30
}

function getPathTermMatchScore(term: string, field: string): number {
  const index = field.indexOf(term)
  if (index < 0) return 0
  if (index === 0) return 100
  const charBefore = field[index - 1]
  if (/\s|\//.test(charBefore)) return 80
  if (index < field.length / 2) return 50
  return 30
}

function getRelevanceScore(query: string, field: string): number {
  const normalizedField = normalizeSearchText(field)
  const normalizedQuery = normalizeSearchText(query)
  if (!normalizedField || !normalizedQuery) return 0

  let bestScore = 0
  const exactIndex = normalizedField.indexOf(normalizedQuery)

  if (exactIndex >= 0) {
    if (exactIndex === 0) {
      bestScore = 110
    } else {
      const charBefore = normalizedField[exactIndex - 1]
      if (/\s/.test(charBefore)) {
        bestScore = 95
      } else if (exactIndex < normalizedField.length / 2) {
        bestScore = 75
      } else {
        bestScore = 60
      }
    }
  }

  const terms = normalizedQuery.split(" ")
  if (terms.length > 1) {
    let matchedTerms = 0
    let termScoreTotal = 0
    for (const term of terms) {
      const termScore = getTermMatchScore(term, normalizedField)
      if (termScore > 0) {
        matchedTerms += 1
        termScoreTotal += termScore
      }
    }
    if (matchedTerms > 0) {
      const coverage = matchedTerms / terms.length
      const averageScore = termScoreTotal / matchedTerms
      const combinedScore = Math.round(averageScore * (0.6 + coverage * 0.4))
      if (combinedScore > bestScore) bestScore = combinedScore
    }
  }

  return bestScore
}

function getPathRelevanceScore(query: string, field: string): number {
  const normalizedField = normalizePathSearchText(field)
  const normalizedQuery = normalizePathSearchText(query)
  if (!normalizedField || !normalizedQuery) return 0

  let bestScore = 0
  const exactIndex = normalizedField.indexOf(normalizedQuery)

  if (exactIndex >= 0) {
    if (exactIndex === 0) {
      bestScore = 110
    } else {
      const charBefore = normalizedField[exactIndex - 1]
      if (/\s|\//.test(charBefore)) {
        bestScore = 95
      } else if (exactIndex < normalizedField.length / 2) {
        bestScore = 75
      } else {
        bestScore = 60
      }
    }
  }

  const terms = normalizedQuery.split(" ")
  if (terms.length > 1) {
    let matchedTerms = 0
    let termScoreTotal = 0
    for (const term of terms) {
      const termScore = getPathTermMatchScore(term, normalizedField)
      if (termScore > 0) {
        matchedTerms += 1
        termScoreTotal += termScore
      }
    }
    if (matchedTerms > 0) {
      const coverage = matchedTerms / terms.length
      const averageScore = termScoreTotal / matchedTerms
      const combinedScore = Math.round(averageScore * (0.6 + coverage * 0.4))
      if (combinedScore > bestScore) bestScore = combinedScore
    }
  }

  return bestScore
}

function getBestScore(query: string, ...fields: (string | null | undefined)[]): number {
  let bestScore = 0
  for (const field of fields) {
    if (field) {
      const score = getRelevanceScore(query, field)
      if (score > bestScore) bestScore = score
    }
  }
  return bestScore
}

function getMoviePathScore(movie: MovieUi, query: string): number {
  const torrentFields: (string | null | undefined)[] = []
  for (const torrent of Object.values(movie.files || {})) {
    torrentFields.push(torrent.title, torrent.playable_file)
  }
  let bestScore = 0
  for (const field of torrentFields) {
    if (!field) continue
    const score = getPathRelevanceScore(query, field)
    if (score > bestScore) bestScore = score
  }
  return bestScore
}

function getSeriesPathScore(series: SeriesUi, query: string): number {
  const torrentFields: (string | null | undefined)[] = []
  for (const season of series.seasons || []) {
    for (const episode of season.episodes || []) {
      for (const torrent of Object.values(episode.files || {})) {
        torrentFields.push(torrent.title, torrent.playable_file)
      }
    }
  }
  let bestScore = 0
  for (const field of torrentFields) {
    if (!field) continue
    const score = getPathRelevanceScore(query, field)
    if (score > bestScore) bestScore = score
  }
  return bestScore
}

// ---------------------------------------------------------------------------
// People matching (mirrored from App.vue)
// ---------------------------------------------------------------------------

interface PersonMatch {
  name: string
  roles: string[]
  highlightRoles: boolean
}

interface PersonCandidate {
  name: string
  role: string
  highlightRoles: boolean
  score: number
  matchedWordIndexes: number[]
}

function getQueryWords(value: string): string[] {
  const normalized = normalizeSearchText(value)
  if (!normalized) return []
  return normalized.split(" ")
}

function getMatchedWordIndexes(queryWords: string[], value: string): number[] {
  const normalized = normalizeSearchText(value)
  if (!normalized || queryWords.length === 0) return []
  const targetWords = normalized.split(" ")
  const matches: number[] = []

  for (let i = 0; i < queryWords.length; i += 1) {
    const queryWord = queryWords[i]
    if (targetWords.some((targetWord) => targetWord.startsWith(queryWord))) {
      matches.push(i)
    }
  }

  return matches
}

function mergePersonMatch(target: PersonMatch[], candidate: PersonCandidate): void {
  const existing = target.find((person) => person.name.toLowerCase() === candidate.name.toLowerCase())
  if (existing) {
    if (!existing.roles.includes(candidate.role)) existing.roles.push(candidate.role)
    if (candidate.highlightRoles) existing.highlightRoles = true
    return
  }

  target.push({
    name: candidate.name,
    roles: [candidate.role],
    highlightRoles: candidate.highlightRoles,
  })
}

function getBestContiguousWordRun(indexes: number[], availableIndexes: Set<number>): number[] {
  const sorted = indexes
    .filter((index) => availableIndexes.has(index))
    .sort((a, b) => a - b)

  if (sorted.length === 0) return []

  let bestStart = 0
  let bestLength = 1
  let runStart = 0
  let runLength = 1

  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i] === sorted[i - 1] + 1) {
      runLength += 1
      continue
    }

    if (runLength > bestLength) {
      bestStart = runStart
      bestLength = runLength
    }

    runStart = i
    runLength = 1
  }

  if (runLength > bestLength) {
    bestStart = runStart
    bestLength = runLength
  }

  return sorted.slice(bestStart, bestStart + bestLength)
}

function nameMatchesQuery(query: string, name: string): boolean {
  const nq = normalizeSearchText(query)
  const nn = normalizeSearchText(name)
  if (!nq || !nn) return false
  // Exact substring match (e.g. "jackie chan" matches "jackie chan" and "jackie chans")
  if (nn.includes(nq)) return true
  // Each query word must appear as a prefix of a name word (word-boundary match)
  const queryWords = nq.split(" ")
  const nameWords = nn.split(" ")
  return queryWords.every((qw) => nameWords.some((nw) => nw.startsWith(qw)))
}

function getNameMatchScore(query: string, name: string): number {
  if (!nameMatchesQuery(query, name)) return 0
  const nq = normalizeSearchText(query)
  const nn = normalizeSearchText(name)
  const idx = nn.indexOf(nq)
  if (idx === 0) return 110
  if (idx > 0) {
    const before = nn[idx - 1]
    if (/\s/.test(before)) return 95
    return 75
  }
  // Prefix-based match: score lower than exact substring
  return 70
}

function matchesPeople(
  query: string,
  cast: { name: string; character?: string | null }[] | null | undefined,
  director?: string | null,
  creators?: string[] | null,
): { matches: PersonMatch[]; score: number } {
  const matchedPeople: PersonMatch[] = []
  const candidates: PersonCandidate[] = []
  const queryWords = getQueryWords(query)

  const addCandidate = (
    name: string,
    role: string,
    highlightRoles: boolean,
    score: number,
    matchedWordIndexes: number[],
  ) => {
    candidates.push({ name, role, highlightRoles, score, matchedWordIndexes })
  }

  let bestScore = 0

  if (director) {
    const score = getNameMatchScore(query, director)
    if (score > 0) {
      addCandidate(director, "Director", false, score, getMatchedWordIndexes(queryWords, director))
    } else {
      addCandidate(director, "Director", false, 0, getMatchedWordIndexes(queryWords, director))
    }
  }

  if (creators) {
    for (const creator of creators) {
      const score = getNameMatchScore(query, creator)
      if (score > 0) {
        addCandidate(creator, "Creator", false, score, getMatchedWordIndexes(queryWords, creator))
      } else {
        addCandidate(creator, "Creator", false, 0, getMatchedWordIndexes(queryWords, creator))
      }
    }
  }

  if (cast) {
    for (const person of cast) {
      const nameScore = getNameMatchScore(query, person.name)
      const characterScore = person.character ? getNameMatchScore(query, person.character) : 0
      const bestPersonScore = Math.max(nameScore, characterScore)
      const nameWordIndexes = getMatchedWordIndexes(queryWords, person.name)
      const characterWordIndexes = person.character
        ? getMatchedWordIndexes(queryWords, person.character)
        : []
      const useCharacterWords = characterWordIndexes.length > nameWordIndexes.length
      const matchedWordIndexes = useCharacterWords ? characterWordIndexes : nameWordIndexes

      if (bestPersonScore > 0) {
        const role = person.character || "Cast"
        const highlightRoles = characterScore > nameScore
        addCandidate(person.name, role, highlightRoles, bestPersonScore, matchedWordIndexes)
      } else {
        addCandidate(person.name, person.character || "Cast", false, 0, matchedWordIndexes)
      }
    }
  }

  for (const candidate of candidates) {
    if (candidate.score <= 0) continue
    mergePersonMatch(matchedPeople, candidate)
    if (candidate.score > bestScore) bestScore = candidate.score
  }

  if (matchedPeople.length > 0) {
    return { matches: matchedPeople, score: bestScore }
  }

  if (queryWords.some((word) => word.length < 2)) {
    return { matches: [], score: 0 }
  }

  const explicitMultiPerson = /[,&+]/.test(query)
  const uncoveredWordIndexes = new Set(queryWords.map((_, index) => index))
  const selected: Array<{ candidate: PersonCandidate; matchedIndexes: number[] }> = []
  const usableCandidates = candidates.filter((candidate) => candidate.matchedWordIndexes.length > 0)
  let hasMultiWordChunk = false

  while (uncoveredWordIndexes.size > 0) {
    let bestCandidate: PersonCandidate | null = null
    let bestChunk: number[] = []
    let bestCoverage = 0

    for (const candidate of usableCandidates) {
      if (selected.some((entry) => entry.candidate === candidate)) continue
      const chunk = getBestContiguousWordRun(candidate.matchedWordIndexes, uncoveredWordIndexes)
      if (chunk.length <= 0) continue
      const coverage = candidate.matchedWordIndexes.length

      if (
        chunk.length > bestChunk.length ||
        (chunk.length === bestChunk.length && coverage > bestCoverage)
      ) {
        bestCandidate = candidate
        bestChunk = chunk
        bestCoverage = coverage
      }
    }

    if (!bestCandidate || bestChunk.length <= 0) break
    selected.push({ candidate: bestCandidate, matchedIndexes: bestChunk })
    if (bestChunk.length > 1) hasMultiWordChunk = true
    for (const index of bestChunk) {
      uncoveredWordIndexes.delete(index)
    }
  }

  if (uncoveredWordIndexes.size > 0 || selected.length === 0) {
    return { matches: [], score: 0 }
  }

  // Without explicit separators, require at least one multi-word person chunk.
  // This avoids accidental matches like "jackie chan" => "Jackie" + "Chan".
  if (!explicitMultiPerson && selected.length > 1 && !hasMultiWordChunk) {
    return { matches: [], score: 0 }
  }

  let multiPersonScore = 0
  for (const entry of selected) {
    mergePersonMatch(matchedPeople, entry.candidate)
    const candidateScore = entry.candidate.score > 0 ? entry.candidate.score : 65
    if (candidateScore > multiPersonScore) multiPersonScore = candidateScore
  }

  return { matches: matchedPeople, score: multiPersonScore }
}

function formatMatchedPeople(people: PersonMatch[]): MatchedPerson[] {
  return people.map((p) => ({
    name: p.name,
    roles: p.roles.join(", "),
    highlightRoles: p.highlightRoles,
  }))
}

// ---------------------------------------------------------------------------
// MediaItem conversion (lightweight, without data payload)
// ---------------------------------------------------------------------------

function movieToSearchResult(movie: MovieUi): SearchResultItem {
  const files = Object.values(movie.files || {})
  const resolution = files.length > 0 ? files[0].resolution : null
  return {
    id: movie.id,
    title: movie.title || "Unknown",
    year: movie.year,
    cover_path: movie.cover_path,
    showreel_images: movie.showreel_images,
    showreel_source_sets: movie.showreel_source_sets,
    type: "movies",
    resolution,
    root_id: movie.root_id,
  }
}

function seriesToSearchResult(series: SeriesUi): SearchResultItem {
  const reelImages: string[] = []
  const reelSourceSets: string[][] = []
  for (const season of series.seasons || []) {
    for (const episode of season.episodes || []) {
      if (episode.reel_sources && episode.reel_sources.length > 0) {
        reelImages.push(episode.reel_sources[0])
        reelSourceSets.push(episode.reel_sources)
      } else if (episode.reel_image) {
        reelImages.push(episode.reel_image)
        reelSourceSets.push([episode.reel_image])
      }
    }
  }
  return {
    id: series.id,
    title: series.title || "Unknown",
    year: null,
    cover_path: series.cover_path,
    showreel_images: reelImages.length > 0 ? reelImages : null,
    showreel_source_sets: reelSourceSets.length > 0 ? reelSourceSets : null,
    type: "series",
    root_id: series.root_id,
  }
}

// ---------------------------------------------------------------------------
// Core search with cancellation token
// ---------------------------------------------------------------------------

const MAX_RESULTS = 100

interface ScoredResult {
  item: SearchResultItem
  score: number
  matchType: "movies" | "series" | "people" | "other"
}

interface CancelToken {
  id: number
}

let currentSearchId = 0

function isCancelled(token: CancelToken): boolean {
  return token.id !== currentSearchId
}

/** Yield control briefly so the worker can receive a new message. */
function yieldControl(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

async function performSearch(
  query: string,
  token: CancelToken,
): Promise<SearchResponseMessage | null> {
  const allScored: ScoredResult[] = []
  const processedIds = new Set<string>()

  const yearMatch = query.match(/^(\d{4})$/)
  const searchYear = yearMatch ? parseInt(yearMatch[1], 10) : null
  const isYearQuery = searchYear !== null && searchYear >= 1900 && searchYear <= 2100
  const yearBonus = 25

  // -------------------------------------------------------------------------
  // Search movies
  // -------------------------------------------------------------------------
  for (let i = 0; i < movies.length; i++) {
    if (i % 50 === 0) {
      if (isCancelled(token)) return null
      await yieldControl()
    }

    const movie = movies[i]
    const titleScore = getBestScore(query, movie.title, movie.info?.original_title)
    const yearScore = isYearQuery && movie.year === searchYear ? yearBonus : 0
    if (titleScore > 0 || yearScore > 0) {
      allScored.push({
        item: movieToSearchResult(movie),
        score: titleScore + yearScore + (movie.info?.rating ?? 0) / 10,
        matchType: "movies",
      })
      processedIds.add(movie.id)
      continue
    }

    const peopleMatch = matchesPeople(query, movie.info?.cast, movie.info?.director)
    if (peopleMatch.matches.length > 0) {
      const item = movieToSearchResult(movie)
      item.searchMatchInfo = { matchedPeople: formatMatchedPeople(peopleMatch.matches) }
      allScored.push({
        item,
        score: peopleMatch.score + (movie.info?.rating ?? 0) / 10,
        matchType: "people",
      })
      processedIds.add(movie.id)
      continue
    }

    const otherScore = Math.max(
      getBestScore(
        query,
        movie.info?.genres?.join(" "),
        movie.info?.keywords?.join(" "),
        movie.info?.overview,
        movie.info?.tagline,
        movie.info?.collection,
      ),
      getMoviePathScore(movie, query),
    )
    if (otherScore > 0) {
      allScored.push({
        item: movieToSearchResult(movie),
        score: otherScore + (movie.info?.rating ?? 0) / 10,
        matchType: "other",
      })
      processedIds.add(movie.id)
    }
  }

  // -------------------------------------------------------------------------
  // Search series
  // -------------------------------------------------------------------------
  for (let i = 0; i < series.length; i++) {
    if (i % 50 === 0) {
      if (isCancelled(token)) return null
      await yieldControl()
    }

    const seriesItem = series[i]
    const titleScore = getBestScore(query, seriesItem.title, seriesItem.info?.original_title)
    const seriesYear = seriesItem.info?.release_date
      ? parseInt(seriesItem.info.release_date.substring(0, 4), 10)
      : null
    const yearScore = isYearQuery && seriesYear === searchYear ? yearBonus : 0
    if (titleScore > 0 || yearScore > 0) {
      allScored.push({
        item: seriesToSearchResult(seriesItem),
        score: titleScore + yearScore + (seriesItem.info?.rating ?? 0) / 10,
        matchType: "series",
      })
      processedIds.add(seriesItem.id)
      continue
    }

    const matchedEpisodes: MatchedEpisode[] = []
    let episodeScore = 0
    const isEndedSingleSeason =
      (seriesItem.info?.number_of_seasons === 1 || seriesItem.seasons?.length === 1) &&
      ["Ended", "Canceled", "Cancelled"].includes(seriesItem.info?.status || "")

    for (const season of seriesItem.seasons || []) {
      for (const episode of season.episodes || []) {
        if (episode.name) {
          const epScore = getRelevanceScore(query, episode.name)
          if (epScore > 0) {
            const hideSeason = isEndedSingleSeason || season.season_number === 0
            const location = hideSeason
              ? `Episode ${episode.episode_number}`
              : `S${season.season_number} Episode ${episode.episode_number}`
            matchedEpisodes.push({
              name: episode.name,
              location,
              seasonNumber: season.season_number,
              episodeNumber: episode.episode_number,
            })
            if (epScore > episodeScore) episodeScore = epScore
          }
        }
      }
    }
    if (matchedEpisodes.length > 0 && !processedIds.has(seriesItem.id)) {
      const item = seriesToSearchResult(seriesItem)
      item.searchMatchInfo = { matchedEpisodes }
      allScored.push({
        item,
        score: episodeScore + (seriesItem.info?.rating ?? 0) / 10,
        matchType: "series",
      })
      processedIds.add(seriesItem.id)
      continue
    }

    const peopleMatch = matchesPeople(query, seriesItem.info?.cast, null, seriesItem.info?.creators)
    if (peopleMatch.matches.length > 0 && !processedIds.has(seriesItem.id)) {
      const item = seriesToSearchResult(seriesItem)
      item.searchMatchInfo = { matchedPeople: formatMatchedPeople(peopleMatch.matches) }
      allScored.push({
        item,
        score: peopleMatch.score + (seriesItem.info?.rating ?? 0) / 10,
        matchType: "people",
      })
      processedIds.add(seriesItem.id)
      continue
    }

    if (!processedIds.has(seriesItem.id)) {
      const otherScore = Math.max(
        getBestScore(
          query,
          seriesItem.info?.genres?.join(" "),
          seriesItem.info?.keywords?.join(" "),
          seriesItem.info?.overview,
          seriesItem.info?.tagline,
          seriesItem.info?.networks?.join(" "),
        ),
        getSeriesPathScore(seriesItem, query),
      )
      if (otherScore > 0) {
        allScored.push({
          item: seriesToSearchResult(seriesItem),
          score: otherScore + (seriesItem.info?.rating ?? 0) / 10,
          matchType: "other",
        })
        processedIds.add(seriesItem.id)
      }
    }
  }

  if (isCancelled(token)) return null

  allScored.sort((a, b) => b.score - a.score)
  const topResults = allScored.slice(0, MAX_RESULTS)

  const moviesCat: SearchResultItem[] = []
  const seriesCat: SearchResultItem[] = []
  const peopleCat: SearchResultItem[] = []
  const otherCat: SearchResultItem[] = []

  for (const scored of topResults) {
    switch (scored.matchType) {
      case "movies":
        moviesCat.push(scored.item)
        break
      case "series":
        seriesCat.push(scored.item)
        break
      case "people":
        peopleCat.push(scored.item)
        break
      case "other":
        otherCat.push(scored.item)
        break
    }
  }

  const categories: SearchCategoryResult[] = []
  if (moviesCat.length > 0) categories.push({ name: "Movies", items: moviesCat })
  if (seriesCat.length > 0) categories.push({ name: "Series", items: seriesCat })
  if (peopleCat.length > 0) categories.push({ name: "People", items: peopleCat })
  if (otherCat.length > 0) categories.push({ name: "Other", items: otherCat })

  return {
    id: token.id,
    results: topResults.map((s) => s.item),
    categories,
  }
}

// ---------------------------------------------------------------------------
// Worker message handler — single persistent runner
// ---------------------------------------------------------------------------

self.onmessage = (event: MessageEvent<SearchWorkerMessage>) => {
  const msg = event.data

  if (msg.type === "index") {
    movies = msg.movies
    series = msg.series
    return
  }

  if (msg.type === "query") {
    const { id, query } = msg
    currentSearchId = id
    const token: CancelToken = { id }

    void (async () => {
      const response = await performSearch(query, token)
      if (!response) {
        return
      }
      self.postMessage(response)
    })()
  }
}
