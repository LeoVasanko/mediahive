# Torrent Manager

Tools for managing a media torrent library: scanning, indexing, metadata fetching, and preview generation.

## Project Structure

```
hivescan/           Indexing & previews (installable package)
  indexer.py          Media index generation
  scanning.py         File system scanning
  parsing.py          Torrent name parsing (PTN)
  models.py           Data models
  images.py           TMDb cover/backdrop downloading
  showreel.py         Video preview clip generation (ffmpeg)
  tmdb_client.py      TMDb API client with caching
  utils.py            Path, size, and timestamp helpers
scripts/
  rtorrent-manager.py Torrent scanning & rtorrent management
rtorrent_client.py    RTorrent XMLRPC/SCGI client
```

## Hivescan

Scans downloaded content, categorizes it (Movies, Series, Other), fetches metadata from TMDb, generates preview clips, and produces a JSON index.

```bash
# Scan downloads, auto-detect common root, create .mediahive folder
hivescan /media/torrents/*

# Scan multiple locations
hivescan /mnt/disk1/* /mnt/disk2/*

# Override output directory
hivescan /media/torrents/* -o /srv/media/.mediahive

# Skip cover/showreel generation
hivescan /media/torrents/* --no-covers --no-showreels
```

## RTorrent Manager

Scans `.torrent` files, filters by tracker, verifies downloads exist on disk, loads verified torrents into rtorrent, and cleans up unregistered torrents.

```bash
# Scan .torrents directories and manage rtorrent
python scripts/rtorrent-manager.py /media/torrents*/.torrents/

# Multiple paths
python scripts/rtorrent-manager.py /mnt/disk1/torrents/.torrents/ /mnt/disk2/torrents/.torrents/

# Filter by tracker, dry run
python scripts/rtorrent-manager.py /media/torrents*/.torrents/ --tracker example.org --dry
```

## Path Mapping

Hivescan auto-detects the common root of scanned paths and creates a `.mediahive` folder there. All paths in the index are stored relative to that root.

| Location | Example |
|----------|---------|
| Torrents | `/media/torrents*/` |
| Index | `/media/.mediahive/index.json` |
| Covers | `/media/.mediahive/movies/` |
| Showreels | `/media/.mediahive/movies/<title>/reel1.webm` |

Use `-o` to override the output directory if the auto-detected root isn't suitable.

## Requirements

- Python ≥ 3.14
- ffmpeg (for showreel generation)

```bash
pip install -e .
```
