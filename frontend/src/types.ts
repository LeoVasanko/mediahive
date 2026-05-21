// Type definitions for the media browser

export type CastGender = 'female' | 'male' | 'non_binary' | 'unknown';

export interface CastMember {
  name: string;
  character?: string | null;
  profile_path: string | null;
  gender?: CastGender | null;
}

export interface SimilarMedia {
  id: number;
  title: string;
  poster_path: string | null;
}

export interface Info {
  tmdb_id: number;
  title: string | null;
  original_title: string | null;
  alternative_titles: string[] | null;
  rating: number | null;
  vote_count: number | null;
  overview: string | null;
  genres: string[] | null;
  release_date: string | null;
  runtime: number | null;
  status: string | null;
  tagline: string | null;
  poster_path: string | null;
  backdrop_path: string | null;
  similar: SimilarMedia[] | null;
  keywords: string[] | null;
  cast: CastMember[] | null;
  director: string | null;
  creators: string[] | null;
  number_of_seasons: number | null;
  number_of_episodes: number | null;
  networks: string[] | null;
}

export interface Torrent {
  title: string | null;
  playable_file: string | null;
  resolution: string | null;
  quality: string | null;
  codec: string | null;
  audio: string | null;
  encoder: string | null;
  size: number | null;
  added_at: number | null;
}

export interface Movie {
  id: string;
  title: string | null;
  info: Info | null;
  year: number | null;
  newest: number | null;
  cover_path: string | null;
  backdrop_path: string | null;
  showreel_images: string[] | null;
  showreel_source_sets: string[][] | null;
  torrents: { [key: string]: Torrent };
}

export interface Episode {
  episode_number: number;
  name: string | null;
  overview: string | null;
  air_date: string | null;
  runtime: number | null;
  still_path: string | null;
  rating: number | null;
  director: string | null;
  reel_image: string | null;
  reel_sources: string[] | null;
  torrents: { [key: string]: Torrent };
}

export interface Season {
  season_number: number;
  name: string | null;
  overview: string | null;
  air_date: string | null;
  poster_path: string | null;
  episode_count: number | null;
  episodes: Episode[];
}

export interface Series {
  id: string;
  title: string | null;
  info: Info | null;
  alternative_titles: string[] | null;
  newest: number | null;
  cover_path: string | null;
  backdrop_path: string | null;
  seasons: Season[];
}

export interface MediaStats {
  total_movies: number;
  total_movie_versions?: number;
  total_series: number;
  total_series_episodes?: number;
}

export interface MediaIndex {
  version: number;
  generated_at: string;
  stats: MediaStats;
  movies: Movie[];
  series: Series[];
}

export type MediaType = 'movies' | 'series' | 'episode';

// Matched person info for search results
export interface MatchedPerson {
  name: string;
  roles: string;  // e.g., "Director", "Tony Stark", "Creator"
  highlightRoles: boolean;  // true if the roles/character matched (vs the name)
}

// Matched episode info for search results
export interface MatchedEpisode {
  name: string;         // Episode name (highlighted)
  location: string;     // "SN Episode M" (dimmed)
  seasonNumber: number; // For navigation to episode
  episodeNumber: number; // For navigation to episode
}

// Info about why a search matched this item
export interface SearchMatchInfo {
  // Matched people with their roles/characters
  matchedPeople?: MatchedPerson[];
  // Matched episodes for series
  matchedEpisodes?: MatchedEpisode[];
}

export interface MediaItem {
  id: string;
  title: string | null;
  year?: number | null;
  cover_path: string | null;
  showreel_images?: string[] | null;
  showreel_source_sets?: string[][] | null;
  type: MediaType;
  resolution?: string | null;
  data: Movie | Series | EpisodeWithSeries;
  // Optional search match info - only present in search results
  searchMatchInfo?: SearchMatchInfo;
}

// Episode with parent series info for standalone display
export interface EpisodeWithSeries {
  episode: Episode;
  series: Series;
  seasonNumber: number;
}

// Task progress info from background scanning
export interface TaskInfo {
  id: string;
  status: string;
  progress: number;
  detail: string;
}

// WebSocket message types (matching server msgspec tagged structs)
export interface WsInitMessage {
  type: 'init';
  data: { movies: Movie[]; series: Series[] };
}

export interface WsUpsertMessage {
  type: 'upsert';
  kind: 'movie' | 'series';
  item: Movie | Series;
}

export interface WsRemoveMessage {
  type: 'remove';
  kind: 'movie' | 'series';
  id: string;
}

export interface WsTaskMessage {
  type: 'task';
  data: TaskInfo;
}

export type WsMessage = WsInitMessage | WsUpsertMessage | WsRemoveMessage | WsTaskMessage;
