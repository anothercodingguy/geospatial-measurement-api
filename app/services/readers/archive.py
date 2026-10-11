import zipfile
from pathlib import Path, PurePosixPath

from app.services.readers.base import InvalidGeospatialFile

MAX_MEMBERS = 1000


def open_zip(path: Path, max_uncompressed_bytes: int) -> zipfile.ZipFile:
    """Open a zip upload after basic safety checks.

    Members are only ever read into memory (never extracted to disk), so path-traversal
    ("zip slip") entries can't escape; we still check sizes to stop zip bombs.
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise InvalidGeospatialFile("File is not a valid zip archive.") from exc

    members = archive.infolist()
    if len(members) > MAX_MEMBERS:
        archive.close()
        raise InvalidGeospatialFile(f"Zip archive has too many entries (> {MAX_MEMBERS}).")
    if sum(m.file_size for m in members) > max_uncompressed_bytes:
        archive.close()
        raise InvalidGeospatialFile("Zip archive is too large when uncompressed.")
    return archive


def data_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Regular files, ignoring directories and macOS metadata (__MACOSX/, ._foo)."""
    result = []
    for info in archive.infolist():
        parts = PurePosixPath(info.filename).parts
        if info.is_dir() or not parts:
            continue
        if parts[0] == "__MACOSX" or parts[-1].startswith("."):
            continue
        result.append(info)
    return result
