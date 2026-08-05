import asyncio
import logging
import tempfile
from pathlib import Path

from app.bazarr.client import BazarrClient
from app.subtitles import srt_io

logger = logging.getLogger(__name__)


# Deliberately NOT under DB_PATH's directory (the persistent /data volume
# in Docker) — this is a disposable cache, not something that needs to
# survive a container restart or live on a mapped share. Lives in the
# container's own ephemeral filesystem layer instead (tempfile.gettempdir()
# resolves to /tmp in the Docker image, the OS temp dir on any other
# platform), so no extra Docker volume/mount is ever needed for this to
# work.
#
# Flat directory, keyed by item_id only — NOT nested per-run. An earlier
# version used a per-run_id subfolder (scratch_root/run_{run_id}/), which
# meant a failed item's cached file was orphaned in that run's folder and
# never found by a LATER run's prefetch (a different run_id → a different,
# empty folder) — defeating the whole point of keeping a failed item's
# cache around for retry. One shared flat directory means any run can find
# and reuse a still-cached file regardless of which run originally fetched
# it.
DEFAULT_SCRATCH_ROOT = Path(tempfile.gettempdir()) / "subtitlarr-scratch"


async def prefetch_source_subtitles(
    client: BazarrClient, ready_items: list[dict], scratch_dir: Path
) -> dict[int, Path]:
    """Ensures every ready item has its source subtitle content cached
    locally, fetching from Bazarr ONLY for items that don't already have a
    valid cached file (e.g. left over from a previous run's failed
    attempt) — so a run's per-item translation work reads from local disk
    instead of hitting Bazarr (and therefore the NAS) once per item. Fetches
    are fired concurrently for whatever's actually missing; read-only local
    NAS/Bazarr traffic, not a rate-limited cloud API, so no pacing/windowing
    is needed.

    Returns {item_id: scratch_file_path} for every item with a usable
    cached file, whether freshly fetched or already present. An item whose
    fetch fails (and has no pre-existing cache) is simply left out of the
    map — translate_item() falls back to fetching directly from Bazarr for
    any item not present here, so one bad fetch doesn't abort the whole
    run."""
    scratch_dir.mkdir(parents=True, exist_ok=True)

    cached: dict[int, Path] = {}
    to_fetch: list[dict] = []
    for entry in ready_items:
        item_id = entry["item"]["id"]
        existing = scratch_dir / f"{item_id}.srt"
        if existing.exists():
            cached[item_id] = existing
        else:
            to_fetch.append(entry)

    async def _fetch_one(entry: dict) -> tuple[int, Path | None]:
        item_id = entry["item"]["id"]
        try:
            cues = await client.get_subtitle_contents(entry["source_path"])
            subs = srt_io.cues_from_bazarr(cues)
            path = scratch_dir / f"{item_id}.srt"
            path.write_bytes(srt_io.compose_srt(subs))
            return item_id, path
        except Exception:  # noqa: BLE001 - one bad fetch must not abort prefetch for the rest
            logger.warning("Prefetch failed for item %d; will fetch live instead", item_id, exc_info=True)
            return item_id, None

    if to_fetch:
        results = await asyncio.gather(*(_fetch_one(entry) for entry in to_fetch))
        for item_id, path in results:
            if path is not None:
                cached[item_id] = path

    return cached


def cleanup_scratch_file(cached_path: Path | None) -> None:
    """Deletes ONE item's scratch file — called only after that item's
    translation succeeds. A failed item's cached source is deliberately
    left in place: prefetch_source_subtitles() will find and reuse it on a
    later retry instead of re-fetching from Bazarr, and it's easier to
    investigate a failure against the exact source that caused it."""
    if cached_path is None:
        return
    try:
        cached_path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove scratch file %s", cached_path, exc_info=True)
