#!/usr/bin/env -S uv run
"""Publish a MediaHive release to Gitea.

Usage:
    uv run scripts/release.py [--draft] [--notes "Release notes"]

Reads from [project.urls] Repository in pyproject.toml.

Token: GITEA_TOKEN environment variable

Steps:
    1. Read the clean tag version via setuptools_scm, find platform artifacts
       in build/ and matching dist/ wheels/sdists
    2. Abort if any dist files are missing
    3. Create a Gitea release for each version (or reuse the existing one
       for the tag, skipping already-uploaded assets) and upload all assets
    4. Remind the user to run: uv publish

Parallel CI platform builds converge on one release per tag; pass --no-dist
on all but one platform so only it uploads the wheel/sdist.
"""

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import httpx
import setuptools_scm

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Config / token helpers
# ---------------------------------------------------------------------------


def load_gitea_config() -> dict:
    pyproject = REPO_ROOT / "pyproject.toml"
    with Path(pyproject).open("rb") as f:
        data = tomllib.load(f)
    repo_url = data.get("project", {}).get("urls", {}).get("Repository")
    if not repo_url:
        raise RuntimeError("[project.urls] Repository missing from pyproject.toml")
    parsed = urlparse(repo_url.rstrip("/"))
    parts = parsed.path.lstrip("/").split("/", 1)
    if len(parts) != 2:
        raise RuntimeError(
            "[project.urls] Repository must include owner and repo, e.g. https://git.example.com/owner/repo"
        )
    return {
        "url": f"{parsed.scheme}://{parsed.netloc}",
        "repo": f"{parts[0]}/{parts[1]}",
    }


def load_token() -> str:
    token = os.environ.get("GITEA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("GITEA_TOKEN environment variable is not set")
    return token


# ---------------------------------------------------------------------------
# ZIP + dist helpers
# ---------------------------------------------------------------------------

# Installer artifacts are versionless (MediaHive-win64-setup.exe,
# MediaHive-macos-setup.pkg, MediaHive-linux-setup.AppImage,
# MediaHive-win64-portable.zip) so /releases/download/latest/<name> links
# stay valid. The version comes from setuptools_scm instead.
_ARTIFACT_RE = re.compile(
    r"^MediaHive-(?!\d)[A-Za-z0-9._-]+\.(?:zip|dmg|exe|pkg|AppImage)$"
)


def read_version() -> str:
    """Read version via setuptools_scm, refusing dev/dirty versions."""
    version = setuptools_scm.get_version(root=str(REPO_ROOT))
    if not re.fullmatch(r"\d+(?:\.\d+)*", version):
        raise RuntimeError(
            f"Refusing to release non-clean version {version!r}. Tag a release first."
        )
    return version


def find_releasable_artifacts() -> list[Path]:
    """Return platform artifact paths in build/."""
    build_dir = REPO_ROOT / "build"
    return [
        p for p in sorted(build_dir.glob("MediaHive-*")) if _ARTIFACT_RE.match(p.name)
    ]


def find_dist_files(version: str) -> list[Path]:
    """Return wheel and sdist paths in dist/ for the given version.

    Raises FileNotFoundError listing every missing file if any are absent.
    """
    dist_dir = REPO_ROOT / "dist"
    wheel = next((p for p in dist_dir.glob(f"mediahive-{version}-*.whl")), None)
    sdist = next(
        (
            p
            for p in dist_dir.glob(f"mediahive-{version}.*")
            if p.suffix in {".gz", ".zip"} and p.name != f"mediahive-{version}.zip"
        ),
        None,
    )
    missing = []
    if wheel is None:
        missing.append(f"  dist/mediahive-{version}-*.whl")
    if sdist is None:
        missing.append(f"  dist/mediahive-{version}.tar.gz (or .zip)")
    if missing:
        raise FileNotFoundError(
            f"Missing dist files for version {version}:\n"
            + "\n".join(missing)
            + "\nRun: uv build"
        )
    return [wheel, sdist]


def find_velopack_feed_files() -> list[Path]:
    """Velopack update feed files produced by vpk pack in build/velopack/.

    Only what the in-app updater (GiteaSource) reads from the latest
    release: this channel's releases.<channel>.json index and the nupkg
    payload it points to. The legacy RELEASES and assets.*.json manifests
    (Squirrel compat / setup bootstrap) are not uploaded.
    """
    releases_dir = REPO_ROOT / "build" / "velopack"
    if not releases_dir.exists():
        return []
    files: list[Path] = []
    for pattern in ("releases.*.json", "*.nupkg"):
        files.extend(sorted(releases_dir.glob(pattern)))
    return files


# ---------------------------------------------------------------------------
# Gitea API helpers
# ---------------------------------------------------------------------------


def gitea_headers(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/json"}


def get_release_by_tag(
    client: httpx.Client, base_url: str, repo: str, tag: str
) -> dict | None:
    """Return the existing release for a tag, or None."""
    url = f"{base_url}/api/v1/repos/{repo}/releases/tags/{tag}"
    resp = client.get(url)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def create_release(
    client: httpx.Client,
    base_url: str,
    repo: str,
    tag: str,
    version: str,
    notes: str,
    draft: bool,
) -> tuple[int, set[str]]:
    """Create a Gitea release, or reuse the existing one for the tag.

    Returns (release_id, names of assets already attached), so parallel
    platform builds can converge on one release without conflicts.
    """
    url = f"{base_url}/api/v1/repos/{repo}/releases"
    payload = {
        "tag_name": tag,
        "name": f"MediaHive {version}",
        "body": notes,
        "draft": draft,
        "prerelease": False,
    }
    resp = client.post(url, json=payload)
    if resp.status_code == 409:
        existing = get_release_by_tag(client, base_url, repo, tag)
        if existing is None:
            raise RuntimeError(f"Release for tag '{tag}' conflicts but cannot be read.")
        release_id = existing["id"]
        assets = {a["name"] for a in existing.get("assets", [])}
        print(f"Release for tag '{tag}' already exists (id={release_id}), reusing it.")
        return release_id, assets
    resp.raise_for_status()
    release_id = resp.json()["id"]
    print(f"Created release id={release_id} (draft={draft})")
    return release_id, set()


def upload_asset(
    client: httpx.Client,
    base_url: str,
    repo: str,
    release_id: int,
    path: Path,
) -> str:
    """Upload a file to the release and return the download URL."""
    url = f"{base_url}/api/v1/repos/{repo}/releases/{release_id}/assets"
    size_mb = path.stat().st_size / (1024 * 1024)
    mime = {
        ".zip": "application/zip",
        ".dmg": "application/x-apple-diskimage",
    }.get(path.suffix, "application/octet-stream")
    print(f"Uploading {path.name} ({size_mb:.1f} MB) ...")
    with Path(path).open("rb") as fh:
        resp = client.post(
            url,
            files={"attachment": (path.name, fh, mime)},
            timeout=300,
        )
    resp.raise_for_status()
    download_url = resp.json()["browser_download_url"]
    print(f"  -> {download_url}")
    return download_url


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    # Windows consoles default to cp1252, which can't encode ✓/✗
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Publish a MediaHive release to Gitea")
    parser.add_argument(
        "--draft", action="store_true", help="Create as a draft release"
    )
    parser.add_argument(
        "--notes", default="", metavar="TEXT", help="Release notes body"
    )
    parser.add_argument(
        "--no-dist",
        action="store_true",
        help="Skip wheel/sdist upload (for parallel platform builds; one job uploads them)",
    )
    args = parser.parse_args()

    try:
        cfg = load_gitea_config()
        token = load_token()
        version = read_version()

        artifacts = find_releasable_artifacts()
        if not artifacts:
            print(
                "No platform artifacts found in build/.\n"
                "Run scripts/guibuild.py first.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate all dist files exist before touching Gitea
        dist_files: list[Path] = [] if args.no_dist else find_dist_files(version)

        base_url = cfg["url"].rstrip("/")
        repo = cfg["repo"]

        with httpx.Client(headers=gitea_headers(token)) as client:
            print(f"\nReleasing {version} ...")
            tag = f"v{version}"
            release_id, uploaded = create_release(
                client, base_url, repo, tag, version, args.notes, args.draft
            )
            for path in dist_files:
                if path.name in uploaded:
                    print(f"Skipping {path.name}, already on the release.")
                    continue
                upload_asset(client, base_url, repo, release_id, path)

            for artifact_path in artifacts:
                if artifact_path.name in uploaded:
                    print(f"Skipping {artifact_path.name}, already on the release.")
                    continue
                print(f"Uploading platform artifact: {artifact_path.name}")
                upload_asset(client, base_url, repo, release_id, artifact_path)
                uploaded.add(artifact_path.name)
                for feed_file in find_velopack_feed_files():
                    if feed_file.name in uploaded:
                        print(f"Skipping {feed_file.name}, already on the release.")
                        continue
                    upload_asset(client, base_url, repo, release_id, feed_file)
                    uploaded.add(feed_file.name)
            print(f"  ✓ {tag} published")

        print("\nDone. To publish to PyPI, run:")
        print("  uv publish")

    except (FileNotFoundError, OSError, RuntimeError, ValueError, httpx.HTTPError) as e:
        print(f"✗ Release failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
