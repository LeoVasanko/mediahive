# Scanner review — findings, fixes, and measured results

Date: 2026-09-03 (review); fixes implemented same day.
Scope: `mediahive/hivescan/*`, `mediahive/index_store.py`, `mediahive/root_registry.py`,
`mediahive/models/events.py`
Method: code review plus instrumented runs of the real `RootScanner` (monkeypatched
timers around every ffmpeg invocation, HTTP request, and filesystem primitive).

Structure of this document:

- **Section 1** records the findings as measured against the *pre-fix* code
  (line references are from that revision).
- **Section 2** describes the fixes that were implemented for each finding.
- **Section 3** gives before/after measurements.
- **Appendix A** (blob storage options) is kept for reference only; it was
  explicitly decided **not** to change the on-disk storage format for now.

Test environment (details omitted intentionally): the media library lives on a
network-mounted filesystem (SMB/CIFS). Two roots were measured:

- **Subset root**: 163 torrents (121 movies, 40 series entries), stale/empty index.
- **Full library root**: existing index with 1127 movies, 159 series,
  1608 episodes, ~2983 video files, ~35k people records.

Environment characteristic that dominates several measurements:
`stat`/`scandir`/`exists` on the network mount are ~0.1 ms (attribute caching
works), but **every small file write costs ~0.2 s** (synchronous write-through).
The scanner writes thousands of small files into `.mediahive/` on that mount.

---

## 1. Findings (pre-fix)

### F1 — Partial rescan replaces whole entries (the "disappearing seasons" bug)

**This is a correctness bug, not a performance issue.**

Chain of events:

1. Discovery mtime-gates per torrent directory/file. Touching one season
   directory of a series yields a `downloads` list containing *only that
   season*.
2. `_process_series` rebuilds the `Series` object from only the items it was
   given.
3. `IndexStore.upsert_series` **replaced the entire entry** and broadcast the
   partial series to all connected clients.

Measured end-to-end on a 5-season series (5 separate season torrents,
110 episodes):

```
full scan        -> index entry seasons [1,2,3,4,5] (110 episodes)
touch season 4   -> rescan 0.2 s, emits ONE upsert: seasons [4] (22 episodes)
                 -> index entry is now seasons [4] — seasons 1-3,5 gone
```

The missing seasons returned only when a scan happened to include all seasons
again — in practice the next **process restart**, because the seen-mtimes map
(F2) was memory-only and forced a full rediscovery at startup.

Same bug class, other variants:

- **Movies**: a touched version directory dropped the other versions of the
  same movie from the listing.
- **TMDb dedupe collapse**: when a partial entry arrived under a *different*
  item id for an already-known TMDb id, the old **complete** entry was
  explicitly deleted.
- **Deletions were never detected**: discovery only ever added to
  `_seen_mtimes`; nothing emitted removals. A torrent deleted from disk stayed
  in the index forever.

### F2 — No persistent scan state: every restart was a full reprocess

All scanner state was process memory: `_seen_mtimes`, the ffmpeg probe cache,
and the episode/playable-file/bluray-probe caches.

Consequences measured:

- **Steady-state rescan within one process: 0.2 s** (subset root, nothing
  changed) — mtime gating worked fine while the process lived.
- **Warm-restart scan (fresh process caches, all disk caches warm): 56.5 s**
  for the same 163 items, of which **52.9 s (94 %) was re-running ffmpeg probes
  on all 469 video files** (21 s `ffmpeg -i` + 32 s `showinfo` passes on HDR
  files). TMDb was 100 % disk-cache hits and cost 0.8 s total.
- Scaled to the full library: every application restart re-probed **~2983
  files ≈ 6–8 minutes** of sequential ffmpeg, during which the whole index was
  re-derived and re-upserted item by item (see F4).

### F3 — Preview (showreel) generation: restart storms and infinite retries

- **Every scan that included an item enqueued all of its reel tasks**, whether
  or not the reels existed. Existence was only checked later by the serial
  worker. A full scan of the subset root enqueued **467** tasks; the full
  library would enqueue ~2700.
- **Nothing was persisted about the queue.** A restart before the queue drained
  started everything over.
- **Failures were never recorded.** In the drain test, 3 of 18 movies failed
  deterministically (DoVi profile 7 titles require `libplacebo` tonemapping,
  which fails on GPU-less machines; one file has a matroska demux error). The
  same files were retried on every subsequent drain — ~1.5 s of probing plus
  crop detection plus an error task broadcast to every client, **forever**.
- The reel-existence check required **all five** reels; short videos
  legitimately produce fewer, so they were treated as "missing" and re-queued
  on every scan, generating nothing new each time.
- Measured generation pace with software AV1 encoding: ~16–28 s per movie
  (5 clips), ~4–5 s per episode clip.
- The reel worker rebroadcast **whole items** built from stale scan data,
  clobbering newer store state.

### F4 — Degraded operation while a full scan is in progress

- Items were re-upserted one by one as processed; combined with F1, any
  partial rescan interleaved with normal use made listings lose data until the
  next restart.
- The showreel worker broadcast an **error task for every permanent failure on
  every scan** (F3), producing user-visible noise.
- The index snapshot was rewritten every 5 s while dirty; with 1286+ items
  that is a ~7.5 MB serialize + write per flush, continuously, for the
  duration of a scan.
- `upsert_*` rebuilt the TMDb-id lookup maps on **every** upsert — O(n²) per
  scan. Measured negligible; fixed anyway as part of the merge work.

### F5 — Cold-scan cost was serialized small-file I/O, not TMDb

Cold scan of the subset root: **975 s for 163 items**.

| Time | Share | Where |
|---|---|---|
| 763 s | 78 % | `download_cast_profile`: 3494 cast images, strictly serialized; ≈0.22 s each ≈ 0.19 s network-mount write + 0.03 s HTTP |
| ~100 s | 10 % | ffmpeg probes (469 files, incl. 83 HDR `showinfo` passes) |
| 73 s | 7 % | TMDb API layer: 217 uncached requests (27 s HTTP) **plus ~41 s writing per-request cache JSON files** to the network mount |
| ~75 s | 8 % | covers / backdrops / season posters (same small-write cost) |
| 0.3 s | — | filesystem discovery walk |

Notes:

- The TMDb disk cache itself is fine: once warm it serves 282 requests in
  0.8 s. Cache *reads* need no optimization.
- The full cast of every title was downloaded sequentially, one tiny file per
  person (the full library has ~35k people records). Re-runs are cheap
  (exists-check), so this was a cold-scan-only cost — but it made the first
  scan of a new root take ~8× longer than everything else combined.

### F6 — Measured as noise (not worth effort)

- The 30-second rescan loop's tree walk: 3.5–6 s per pass over the full
  library (~1900 directories). Continuous but light.
- `get_directory_size` per torrent: 0.1 s total in the scan.
- TMDb disk-cache reads: sub-second per scan.
- `trigger_scan` was dead code — nothing called it.
- `_seen_mtimes` was updated *before* processing; a cancelled/failed scan
  permanently lost that update until the next restart.
- The in-process probe cache was keyed by path only; a replaced file kept
  stale probe data until restart.

---

## 2. Implemented fixes

All proposals P1–P5 from the review were implemented, keeping the existing
on-disk format unchanged (no blob storage — see Appendix A).

### F1 → merge-semantics upserts + deletion sync

- The `Upsert` event now carries `scanned: list[str]` — the media-root-relative
  torrent paths whose content was (re)scanned to build the item
  (`mediahive/models/events.py`; `_process_movies`/`_process_series` yield it).
- `IndexStore.upsert_movie/upsert_series` (`mediahive/index_store.py`) merge a
  partial rebuild into the existing entry instead of replacing it:
  file entries belonging to scanned torrents are replaced, everything else is
  preserved, episodes/seasons emptied by the merge are dropped, same-episode
  multi-release files are unioned, and non-None scalar fields from the fresh
  scan win. The store broadcasts only when the merged result actually changed.
- The TMDb dedupe collapse now folds duplicates through the same merge (the
  complete entry's scalars win), so a partial candidate can no longer delete a
  complete entry; the reverse TMDb-id map is fixed up incrementally instead of
  rebuilding both maps per upsert (also F4/P5).
- **Deletion sync**: discovery collects the full set of candidate torrent
  paths; after a fully completed scan the scanner emits a new `Sync` event and
  `IndexStore.sync_torrent_paths` drops file entries whose torrent path is
  gone, cascading to empty episodes/seasons/items with proper removals.

### F2 → persisted scan state and probe cache

Three small JSON files under `.mediahive/` per root, loaded at scanner start
and written atomically (tmp + rename) **only when changed**:

- `scan-state.json`: relpath → mtime. A restart over an unchanged library now
  discovers "0 new items" and finishes in walk time. Mtimes are committed
  **after** the scan completes successfully (fixes the pre-commit nit from F6):
  a cancelled/failed scan retries its items.
- `probe-cache.json`: path → {mtime, size, probe fields} for every probed
  file, **failures included**. Keying by mtime+size makes it self-invalidating
  when a file is replaced (also fixes the stale-probe nit from F6). Non-plain
  paths (bluray:/concat: URIs) fail `stat` and stay memory-cached only.
- `reel-state.json`: see F3 below.

### F3 → reel-state persistence, backoff, and queue gating

- `reel-state.json` records, per media folder (movies) or per episode
  (`folder#SxxEyy`), the video's mtime+size, status (`done`/`failed`),
  attempt count, and last-attempt timestamp.
- `_reel_needed` gates both scan-time queueing and the worker:
  `done` entries are skipped; `failed` entries back off exponentially
  (6 h → 12 h → … capped at 1 week); entries with no record fall back to a
  cheap on-disk existence check, and existing reels are silently recorded as
  `done` so future scans take the cheap path. This ends both the infinite
  retries of unreadable files and the re-queueing of short videos.
- The worker no longer rebroadcasts whole (stale) items: it sends narrow
  `MovieShowreel` / `EpisodeReel` events, and the store updates only the reel
  fields of the current entry (`set_movie_showreel` / `set_episode_reel`).
- Reel state is persisted at scan finalize, on scanner `stop()`, and as soon
  as the reel queue drains (a crash between scans no longer loses records).

### F5 → bounded parallelism for downloads and TMDb fetches

- Cast-profile downloads: `asyncio.gather` with a semaphore of 8.
- TMDb title lookups (movies and series) are prefetched in parallel
  (semaphore of 4) before the grouping loops, which then read the per-call
  caches.
- Season-detail fetches are prefetched in parallel (semaphore of 4) before the
  season loop.
- Per-item cover/backdrop/poster logic is unchanged (exists-check-fast when
  warm).

### Housekeeping (P5)

- Dead `trigger_scan` removed (`is_scanning` kept).
- TMDb-id index bookkeeping is incremental (see F1 above).
- `_rebuild_tmdb_indexes` remains only for snapshot load and dedupe.

---

## 3. Measured results

Subset root (163 items, 121 movies / 40 series entries, 469 video files),
same network mount:

| Scenario | Before | After |
|---|---|---|
| Touch one season of a 5-season series | other 4 seasons vanish until next full scan | 0.3 s rescan, one partial upsert (`scanned=[S04]`), store keeps all 5 seasons |
| Steady rescan, nothing changed (same process) | 0.2 s | 0.2 s, 0 upserts, no writes |
| Warm restart, unchanged library (fresh process) | 56.5 s (94 % ffmpeg re-probes) + 467 reel tasks queued | **0.2 s, 0 upserts, reel queue 0** |
| First scan with warm TMDb cache but no probe cache | 56.5 s | 51.6 s once — writes `probe-cache.json` (469 records, 207 KB); subsequent runs skip all probing |
| Permanently unreadable files (3 DoVi/libplacebo movies) | retried on every scan and every startup, error broadcast each time | recorded as failed once, skipped within backoff |
| Cold scan, nothing cached | 975 s | not re-measured end-to-end; the dominant terms are now 8-way parallel (cast images: 3494 downloads measured at 3.7 s when warm) |
| Deleted torrent | stayed in index forever | removed on the next completed scan via `Sync` |

Unit-level checks (synthetic `IndexStore` + scanner state, no filesystem
library involved): partial upsert preserves untouched seasons and replaces
rescanned torrent files; multi-release episode union; sync removal cascades;
single-episode reel updates; probe-cache save/load roundtrip; reel backoff
math; reel-state persistence roundtrip across scanner instances.

---

## Appendix A — Blob storage options: scan-time vs runtime concerns

**Status: reference only — not implemented.** The current on-disk format
(one file per artifact) was deliberately kept. This appendix stays as
documentation of the options if write amplification or file counts ever
become an operational issue.

The two concerns have opposite constraints, so they should be decided
separately:

- **Scan-time data** is written and read only by the scanner. Nothing in the
  server serves it. Storage format is therefore a pure implementation detail
  and can be changed freely.
- **Runtime data** is delivered to the frontend as plain files via
  `/api/assets/{root}/{movies|series|people}/{path}` (covers, posters,
  backdrops, person photos) and `/api/media/{root}/{path}` (media files), with
  etag/range streaming. Anything that replaces files here must keep an HTTP
  serving story working.

### A.1 Artifact inventory (measured)

| Artifact | Class | Avg size | Count (full library) | Total | Written |
|---|---|---|---|---|---|
| TMDb response cache JSON | scan-time | 25 KB (median 9 KB) | ~8,500 | ~215 MB | on every uncached API request |
| Probe results / scan state / reel state | scan-time | ~0.5 KB/record | ~3,000 records | ~1.5 MB | per processed file |
| Person photos | runtime | 11.4 KB | ~22,500 | ~262 MB | once per person |
| `cover.jpg` / season posters | runtime | ~77 KB | ~2,100 | ~164 MB | once per title/season |
| `backdrop.jpg` | runtime | ~147 KB | ~1,460 | ~220 MB | once per title |
| Reel clips (WebM/AV1) | runtime | ~450 KB | ~8,700 | ~4.0 GB | once per title/episode |
| `index.json` snapshot | both | 7.5 MB | 1 | 7.5 MB | debounced, only when dirty |

Reference point for the write-amplification math: on the network mount one
small-file write costs ~0.2 s, while a single 7.5 MB sequential write costs
the same ~0.2 s (~40 MB/s). So ~31,000 tiny files ≈ 1.7 hours of serialized
write time, versus ~6 s for the same bytes as one bulk dump.

### A.2 Scan-time blob store (TMDb cache, probe cache, scan state)

Nothing here is served, so the only requirement is fast lookup + cheap
persistence. Two workable shapes:

- **RAM map + debounced atomic dump.** Plain dicts keyed by request hash /
  file path, dumped as one binary file (length-prefixed msgspec or JSON blob,
  optionally zstd-compressed) with tmp-write + rename, on the same
  dirty-flag + debounce discipline `index.json` already uses. Effects: the
  ~8,500 individual cache writes collapse into a handful of bulk flushes;
  warm lookups become dict hits with zero filesystem calls. TMDb JSON
  compresses ~10× (215 MB → ~20–25 MB), so a full dump is a sub-second write.
  Caveat: holding all responses parsed in RAM costs ~200 MB for the full
  library; storing raw response *bytes* and parsing lazily, or capping to
  entries referenced by known index items, keeps this modest.
- **SQLite (stdlib, WAL mode).** One database file, incremental commits, crash
  safety without full dumps, and kernel page cache instead of explicit RAM
  management. Better fit if the cache is allowed to grow unbounded, at the
  price of slightly more code.

Either way, keep the existing cache *semantics* unchanged: cache HTTP-level
failures, never cache network errors. With persisted scan state in place the
TMDb cache becomes write-rarely (new items only), which further lowers the
value of elaborate engineering here — the simple dump is likely enough.

### A.3 Runtime-served artifacts

- **Reels, covers, backdrops, season posters: keep as files.** They are few
  per title, tens-to-hundreds of KB, written exactly once, and benefit from
  the existing etag/range file serving. No write-amplification problem.
- **Person photos** (~22.5k files × 11.4 KB) are the one runtime class where
  tiny files hurt at scan time. Three options, in increasing invasiveness:
  1. **Keep files, fix only the scan-time behavior** — bounded-parallel
     downloads, optionally capped to top-N billed cast. Zero changes to
     serving; the cold-scan cost drops ~8× but the file count stays.
     *(This is the option currently implemented.)*
  2. **Blob db + serve from the db.** Person photos move into the same store
     as above; the assets handler gains one branch for the `people` asset
     type that streams bytes from the db instead of the filesystem.
     Eliminates all 22.5k tiny files. If full RAM residency (262 MB) is
     undesirable, use SQLite and let the page cache handle it.
  3. **Hybrid lazy materialization.** The db is authoritative at scan time
     (no tiny writes during scans); the assets handler writes the photo to the
     conventional path on first request and serves it as a file thereafter.
     Serving logic and URLs stay unchanged; disk usage appears only for
     people actually viewed.

Note that `index.json` itself is already the right shape: one atomic 7.5 MB
file, rewritten only when dirty. The goal for everything else is simply to
reach the same shape per concern.

### A.4 Recommendation (if revisited)

| Concern | Recommended treatment |
|---|---|
| TMDb response cache | RAM map + debounced single-file dump (SQLite if growth matters) |
| Probe cache, seen-mtimes, reel-failure records | same dump mechanism, separate small files *(currently: three small JSON files, written only when dirty)* |
| Person photos | option 1 now (parallel + lazy fetching); option 2 or 3 if file count becomes an operational issue |
| Covers, posters, backdrops, reels | unchanged — plain files |
| `index.json` | unchanged |
