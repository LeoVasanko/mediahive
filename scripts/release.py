#!/usr/bin/env -S uv run
"""Publish a MediaHive release to Gitea.

Usage:
    uv run scripts/release.py [--draft] [--notes "Release notes"]

Reads from [project.urls] Repository in pyproject.toml.

Token: GITEA_TOKEN environment variable

Steps:
    1. Find clean-versioned ZIPs in build/ and matching dist/ wheels/sdists
    2. Abort if any dist files are missing for a found ZIP version
    3. Create a Gitea release for each version and upload all assets
    4. Remind the user to run: uv publish
"""

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import httpx

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

# Matches MediaHive-1.2.3-win64.zip, MediaHive-1.2.3-macos-arm64.zip, etc.
# Rejects dev/dirty versions like MediaHive-1.2.3.dev0+gabcd-win64.zip
_CLEAN_ZIP_RE = re.compile(r"^MediaHive-(\d+(?:\.\d+)*)-([A-Za-z0-9._-]+)\.zip$")


def find_releasable_zips() -> list[tuple[Path, str, str]]:
    """Return (path, version, platform_tag) for clean-versioned ZIPs in build/."""
    build_dir = REPO_ROOT / "build"
    results = []
    for p in sorted(build_dir.glob("MediaHive-*.zip")):
        m = _CLEAN_ZIP_RE.match(p.name)
        if m:
            results.append((p, m.group(1), m.group(2)))
    return results


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


# ---------------------------------------------------------------------------
# Gitea API helpers
# ---------------------------------------------------------------------------


def gitea_headers(token: str) -> dict:
    return {"Authorization": f"token {token}", "Accept": "application/json"}


def create_release(
    client: httpx.Client,
    base_url: str,
    repo: str,
    tag: str,
    version: str,
    notes: str,
    draft: bool,
) -> int:
    """Create a Gitea release and return its id."""
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
        raise RuntimeError(f"A release for tag '{tag}' already exists on Gitea.")
    resp.raise_for_status()
    release_id = resp.json()["id"]
    print(f"Created release id={release_id} (draft={draft})")
    return release_id


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
    mime = "application/zip" if path.suffix == ".zip" else "application/octet-stream"
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
    args = parser.parse_args()

    try:
        cfg = load_gitea_config()
        token = load_token()

        zips = find_releasable_zips()
        if not zips:
            print(
                "No clean-versioned ZIPs found in build/.\n"
                "Run scripts/guibuild.py first.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Validate all dist files exist before touching Gitea
        dist_files: dict[str, list[Path]] = {}
        for _, version, _platform_tag in zips:
            dist_files[version] = find_dist_files(version)

        base_url = cfg["url"].rstrip("/")
        repo = cfg["repo"]

        with httpx.Client(headers=gitea_headers(token)) as client:
            release_ids_by_version: dict[str, int] = {}
            for zip_path, version, platform_tag in zips:
                print(f"\nReleasing {version} ...")
                tag = f"v{version}"
                release_id = release_ids_by_version.get(version)
                if release_id is None:
                    release_id = create_release(
                        client, base_url, repo, tag, version, args.notes, args.draft
                    )
                    release_ids_by_version[version] = release_id
                    for path in dist_files[version]:
                        upload_asset(client, base_url, repo, release_id, path)

                print(f"Uploading platform artifact: {platform_tag}")
                upload_asset(client, base_url, repo, release_id, zip_path)
                print(f"  ✓ {tag} published")

        print("\nDone. To publish to PyPI, run:")
        print("  uv publish")

    except (FileNotFoundError, OSError, RuntimeError, ValueError, httpx.HTTPError) as e:
        print(f"✗ Release failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
