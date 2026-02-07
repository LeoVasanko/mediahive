import type { MediaIndex } from './types';

/**
 * Load the media index from the server
 */
export async function loadMediaIndex(): Promise<MediaIndex> {
  const response = await fetch('/api/index');
  if (!response.ok) {
    throw new Error(`Failed to load media index: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Play a media file with the system's default player
 */
export async function playMedia(filePath: string): Promise<void> {
  try {
    const response = await fetch('/api/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: filePath }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || response.statusText);
    }
  } catch (e) {
    console.error('Play media error:', e);
    alert(`Failed to play: ${e}`);
  }
}

/**
 * Open a folder in Windows Explorer
 */
export async function openFolder(folderPath: string): Promise<void> {
  try {
    const response = await fetch('/api/open-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderPath }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || response.statusText);
    }
  } catch (e) {
    console.error('Open folder error:', e);
    alert(`Failed to open folder: ${e}`);
  }
}

/**
 * Convert a cover path to a displayable URL.
 * Uses FastAPI server for async file serving.
 *
 * The path comes from the server already converted to Windows format (Z:\...)
 * Paths starting with '/' are TMDB relative paths that weren't fetched - ignore them
 */
export function getCoverUrl(coverPath: string | null): string {
  if (!coverPath) {
    return '';
  }
  // Ignore TMDB relative paths (start with /) - these are bugs in the index
  if (coverPath.startsWith('/')) {
    return '';
  }

  // Convert relative path to URL path for FastAPI server
  // .mediahive/covers/Movies/... -> /media/.mediahive/covers/Movies/...
  let urlPath = coverPath;

  // Remove drive letter (Z:) and convert backslashes to forward slashes
  if (urlPath.match(/^[A-Za-z]:/)) {
    urlPath = urlPath.substring(2);
  }
  urlPath = urlPath.replace(/\\/g, '/');

  // Ensure path starts with /
  if (!urlPath.startsWith('/')) {
    urlPath = '/' + urlPath;
  }

  // Encode URI components but preserve slashes
  const encodedPath = urlPath.split('/').map(segment => encodeURIComponent(segment)).join('/');

  return `/api/media${encodedPath}`;
}
