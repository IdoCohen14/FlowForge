"""Store validated uploads under generated names in a shared local directory."""

import os
import re
import tempfile
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from dotenv import load_dotenv

from csv_analysis import validate_csv

PROJECT_ROOT = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


class FileStorageError(ValueError):
    pass


class UploadTooLarge(FileStorageError):
    pass


class UnsupportedFileType(FileStorageError):
    pass


def get_upload_dir() -> Path:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    directory = Path(os.getenv("UPLOAD_DIR", str(PROJECT_ROOT / "uploads")))
    if not directory.is_absolute():
        raise FileStorageError("UPLOAD_DIR must be an absolute path")
    return directory.resolve()


def resolve_storage_path(storage_filename: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.csv", storage_filename):
        raise FileStorageError("Invalid storage filename")
    directory = get_upload_dir()
    path = (directory / storage_filename).resolve()
    if not path.is_relative_to(directory):
        raise FileStorageError("Storage path must remain inside UPLOAD_DIR")
    return path


def store_csv(source: BinaryIO, client_filename: str) -> str:
    """Return a generated filename; the caller retains ownership of source."""
    if not client_filename.lower().endswith(".csv"):
        raise UnsupportedFileType("Only .csv files are supported")
    directory = get_upload_dir()
    temporary_path: Path | None = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid4().hex}.csv"
        final_path = resolve_storage_path(filename)
        if final_path.exists():
            raise FileStorageError("Storage filename already exists")
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as target:
            temporary_path = Path(target.name)
            size = 0
            while chunk := source.read(CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadTooLarge("CSV exceeds the 10 MiB upload limit")
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        validate_csv(temporary_path)
        # Same-directory rename gives atomic visibility; Windows rejects collisions.
        temporary_path.rename(final_path)
        return filename
    except OSError:
        raise FileStorageError("Could not store CSV file") from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
