"""Recognizable, locked scratch directories with conservative crash recovery.

Only flat generated input/output filenames belong here. Never recursively remove
unknown data, follow links, or infer that an old directory means an inactive job.
"""

import logging
import os
import re
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)
PREFIXES = {"artifact-stage-", "artifact-upload-", "artifact-validated-"}
IDENTITY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MEMBER = re.compile(r"(?:input|output)-[0-2]\.(?:xlsx|docx|pdf|png)")


def _native_root(root: Path) -> Path:
    root = Path(root).absolute()
    if os.name == "nt" and not str(root).startswith("\\\\?\\"):
        root = Path("\\\\?\\" + str(root))
    return root


def _plain(path, *, directory=False):
    info = path.lstat()
    linked = stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
    return not linked and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))


def _lock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream):
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _members(folder):
    if not _plain(folder, directory=True):
        return None
    entries = list(folder.iterdir())
    if any((p.name != ".lease" and not MEMBER.fullmatch(p.name)) or not _plain(p) for p in entries):
        return None
    return entries


def _remove_payload(entries):
    for path in entries:
        if path.name != ".lease":
            path.unlink(missing_ok=True)


def _remove_empty_workspace(folder):
    # Windows can report a transient sharing violation immediately after closing
    # the lease/deleting a scanned file. Retry only this error on the same empty
    # directory; never broaden cleanup to recursive deletion.
    for attempt in range(4):
        try:
            (folder / ".lease").unlink(missing_ok=True)
            folder.rmdir()
            return
        except FileNotFoundError:
            return
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 32 or attempt == 3:
                raise
            time.sleep(0.02 * (attempt + 1))


@contextmanager
def temporary_workspace(root: Path, prefix: str):
    root = _native_root(root)
    if prefix not in PREFIXES or not _plain(root, directory=True):
        raise ValueError("Invalid artifact temporary storage root or prefix")
    folder = root / f"{prefix}{uuid4()}"
    folder.mkdir(mode=0o700)
    lease = (folder / ".lease").open("x+b")
    lease.write(b"1")
    lease.flush()
    _lock(lease)
    entries = None
    try:
        yield folder
    finally:
        try:
            entries = _members(folder)
            if entries is not None:
                _remove_payload(entries)
        finally:
            _unlock(lease)
            lease.close()
        if entries is not None:
            try:
                _remove_empty_workspace(folder)
            except OSError as error:
                logger.warning("Artifact scratch cleanup deferred for %s: %s", folder.name, error)


def reap_workspaces(root: Path, prefix: str, *, grace_seconds=3600, limit=1000, dry_run=False):
    root = _native_root(root)
    if prefix not in PREFIXES or grace_seconds < 3600 or limit < 1:
        raise ValueError("Invalid artifact scratch recovery bounds")
    if not _plain(root, directory=True):
        return 0
    cutoff, removed = time.time() - grace_seconds, 0
    for folder in root.iterdir():
        if not folder.name.startswith(prefix) or not IDENTITY.fullmatch(folder.name[len(prefix) :]):
            continue
        lease = None
        locked = False
        try:
            if not _plain(folder, directory=True) or folder.stat().st_mtime >= cutoff:
                continue
            entries = _members(folder)
            if entries is None:
                continue
            marker = folder / ".lease"
            if marker.exists():
                lease = marker.open("r+b")
                _lock(lease)
                locked = True
            # Recheck after obtaining the lease; an active job may have just finished.
            if folder.stat().st_mtime >= cutoff or _members(folder) is None:
                continue
            if not dry_run:
                _remove_payload(entries)
            removed += 1
        except OSError:
            # A held lease, a concurrent reaper, or inaccessible storage is not ours to delete.
            continue
        finally:
            if locked:
                _unlock(lease)
            if lease is not None:
                lease.close()
        if not dry_run:
            try:
                _remove_empty_workspace(folder)
            except OSError as error:
                logger.warning(
                    "Artifact scratch cleanup could not finish %s: %s", folder.name, error
                )
        if removed >= limit:
            break
    return removed
