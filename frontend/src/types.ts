// Type definitions for the media browser

export type CastGender = "female" | "male" | "non_binary" | "unknown"

export interface CastMember {
  name: string
  character?: string | null
  profile_path: string | null
  gender?: CastGender | null
  id?: number | null
}

export type CastCreditWire = [character: string | null, id: number | null]
export type PersonWire = [name: string, profile_path: string | null, gender: CastGender | null]

export interface Person {
  name: string
  profile_path: string | null
  gender?: CastGender | null
}

export interface Info {
  tmdb_id: number
  title: string | null
  original_title: string | null
  original_language: string | null
  alternative_titles: string[] | null
  rating: number | null
  vote_count: number | null
  overview: string | null
  genres: string[] | null
  release_date: string | null
  runtime: number | null
  collection: string | null
  status: string | null
  tagline: string | null
  keywords: string[] | null
  cast: CastMember[] | null
  director: string | null
  creators: string[] | null
  number_of_seasons: number | null
  number_of_episodes: number | null
  networks: string[] | null
}

export interface Torrent {
  title: string | null
  playable_file: string | null
  resolution: string | null
  quality: string | null
  network: string | null
  codec: string | null
  audio: string | null
  audio_languages: string[] | null
  subtitle_languages: string[] | null
  hdr?: boolean
  dovi?: boolean
  atmos?: boolean
  hdr10plus?: boolean
  encoder: string | null
  size: number | null
  added_at: number | null
  root_id?: string | null
}

export interface Movie {
  title: string | null
  info: Info | null
  year: number | null
  newest: number | null
  cover_path: string | null
  backdrop_path: string | null
  showreel_images: string[] | null
  showreel_source_sets: string[][] | null
  files: { [key: string]: Torrent }
}

export interface Episode {
  episode_number: number
  name: string | null
  overview: string | null
  air_date: string | null
  runtime: number | null
  still_path: string | null
  rating: number | null
  director: string | null
  reel_image: string | null
  reel_sources: string[] | null
  files: { [key: string]: Torrent }
}

export interface Season {
  season_number: number
  name: string | null
  overview: string | null
  air_date: string | null
  poster_path: string | null
  episode_count: number | null
  episodes: Episode[]
}

export interface Series {
  title: string | null
  info: Info | null
  alternative_titles: string[] | null
  newest: number | null
  cover_path: string | null
  backdrop_path: string | null
  seasons: Season[]
}

/** A series' single continue point (last watched position). */
export interface SeriesResumePoint {
  seasonNumber: number
  episodeNumber: number
  positionSeconds: number
}

export interface MovieUi extends Movie {
  id: string
  root_id: string | null
}

export interface SeriesUi extends Series {
  id: string
  root_id: string | null
}

export interface MediaStats {
  total_movies: number
  total_movie_versions?: number
  total_series: number
  total_series_episodes?: number
}

export interface MediaIndex {
  v: number
  generated_at: string
  movies: MovieUi[]
  series: SeriesUi[]
}

export type MediaType = "movies" | "series" | "episode"

// Matched person info for search results
export interface MatchedPerson {
  name: string
  roles: string // e.g., "Director", "Tony Stark", "Creator"
  highlightRoles: boolean // true if the roles/character matched (vs the name)
}

// Matched episode info for search results
export interface MatchedEpisode {
  name: string // Episode name (highlighted)
  location: string // "SN Episode M" (dimmed)
  seasonNumber: number // For navigation to episode
  episodeNumber: number // For navigation to episode
}

// Info about why a search matched this item
export interface SearchMatchInfo {
  // Matched people with their roles/characters
  matchedPeople?: MatchedPerson[]
  // Matched episodes for series
  matchedEpisodes?: MatchedEpisode[]
}

export interface MediaItem {
  id: string
  title: string | null
  year?: number | null
  cover_path: string | null
  showreel_images?: string[] | null
  showreel_source_sets?: string[][] | null
  type: MediaType
  resolution?: string | null
  data: Movie | Series | EpisodeWithSeries
  root_id: string | null
  // Optional search match info - only present in search results
  searchMatchInfo?: SearchMatchInfo
}

// Episode with parent series info for standalone display
export interface EpisodeWithSeries {
  episode: Episode
  series: SeriesUi
  seasonNumber: number
}

// Task progress info from background scanning
export interface TaskInfo {
  id: string
  status: string
  progress: number
  detail: string
}

// WebSocket message types (matching server msgspec tagged structs)
export interface WsRootStatus {
  root_id: string
  path: string
  status: string
  error: string | null
  snapshot_loaded: boolean
  movies: number
  series: number
}

export interface WsRootInitData {
  movies: Record<string, Movie>
  series: Record<string, Series>
  people?: Record<string, PersonWire>
}

export interface WsRootsMessage {
  type: "roots"
  roots: WsRootStatus[]
}

export interface WsInitMessage {
  type: "init"
  roots: Record<string, WsRootInitData>
}

export interface WsUpsertMessage {
  type: "upsert"
  root_id: string
  kind: "movie" | "series"
  id: string
  item: Movie | Series
  people?: Record<string, PersonWire>
}

export interface WsRemoveMessage {
  type: "remove"
  root_id: string
  kind: "movie" | "series"
  id: string
}

export interface WsTaskMessage {
  type: "task"
  root_id: string
  data: TaskInfo
}

export type WsMessage =
  | WsRootsMessage
  | WsInitMessage
  | WsUpsertMessage
  | WsRemoveMessage
  | WsTaskMessage
