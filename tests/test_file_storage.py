from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest

import file_storage
from csv_analysis import CsvValidationError, MAX_COLUMNS, MAX_FIELD_CHARACTERS
from file_storage import FileStorageError, resolve_storage_path, store_csv


@pytest.fixture
def upload_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "uploads"
    monkeypatch.setenv("UPLOAD_DIR", str(directory))
    return directory


@pytest.mark.parametrize("data", [b"name,value\nAlice,2\n", b"\xef\xbb\xbfname,value\r\nAlice,2\r\n", b'name,value\n"Alice, Bob",2\n', b"name,value\n"])
def test_valid_csv_storage(upload_dir: Path, data: bytes) -> None:
    filename = store_csv(BytesIO(data), "dataset.csv")
    assert UUID(filename[:-4]).version == 4
    assert resolve_storage_path(filename).read_bytes() == data
    assert list(upload_dir.iterdir()) == [upload_dir / filename]


@pytest.mark.parametrize("name", ["../../escape.csv", "C:\\Windows\\escape.csv", "CON.csv", "..\\escape.csv", "/absolute.csv"])
def test_client_name_never_becomes_storage_path(upload_dir: Path, name: str) -> None:
    filename = store_csv(BytesIO(b"a\n1\n"), name)
    assert resolve_storage_path(filename).parent == upload_dir
    assert list(upload_dir.iterdir()) == [upload_dir / filename]


@pytest.mark.parametrize("data", [b"", b"\n", b"a,\n1,2\n", b"a,a\n1,2\n", b"a, a\n1,2\n", b"a,b\n1\n", b"a\n1,2\n", b'a\n"unfinished\n', b"a\n\xff\n", b"a\n\x00\n", b"a\n\n"])
def test_invalid_csv_removes_partial_file(upload_dir: Path, data: bytes) -> None:
    with pytest.raises(CsvValidationError):
        store_csv(BytesIO(data), "dataset.csv")
    assert list(upload_dir.iterdir()) == []


@pytest.mark.parametrize("data", [(",".join(f"c{i}" for i in range(MAX_COLUMNS + 1)) + "\n").encode(), b"a\n" + b"x" * (MAX_FIELD_CHARACTERS + 1)], ids=["too-many-columns", "field-too-long"])
def test_csv_limits(upload_dir: Path, data: bytes) -> None:
    with pytest.raises(CsvValidationError):
        store_csv(BytesIO(data), "dataset.csv")
    assert list(upload_dir.iterdir()) == []


def test_oversized_stream_stops_before_end(upload_dir: Path) -> None:
    source = BytesIO(b"x" * (file_storage.MAX_UPLOAD_BYTES + file_storage.CHUNK_BYTES * 2))
    with pytest.raises(FileStorageError, match="10 MiB"):
        store_csv(source, "large.csv")
    assert source.tell() < len(source.getbuffer())
    assert list(upload_dir.iterdir()) == []


def test_wrong_extension(upload_dir: Path) -> None:
    with pytest.raises(FileStorageError, match=".csv"):
        store_csv(BytesIO(b"a\n1"), "file.exe")
    assert not upload_dir.exists()


@pytest.mark.parametrize("filename", ["../file.csv", "C:\\file.csv", "file.csv", "a" * 32 + "/file.csv"])
def test_storage_path_rejects_untrusted_names(upload_dir: Path, filename: str) -> None:
    with pytest.raises(FileStorageError):
        resolve_storage_path(filename)


def test_relative_upload_dir_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPLOAD_DIR", "relative/uploads")
    with pytest.raises(FileStorageError, match="absolute"):
        file_storage.get_upload_dir()


def test_stream_failure_cleans_up(upload_dir: Path) -> None:
    class BrokenStream(BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise OSError("Sensitive internal path")

    with pytest.raises(FileStorageError, match="^Could not store CSV file$"):
        store_csv(BrokenStream(), "file.csv")
    assert list(upload_dir.iterdir()) == []


def test_finalization_failure_cleans_up(upload_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_rename(self: Path, target: Path) -> None:
        raise OSError("Sensitive path")

    monkeypatch.setattr(Path, "rename", fail_rename)
    with pytest.raises(FileStorageError):
        store_csv(BytesIO(b"a\n1\n"), "file.csv")
    assert list(upload_dir.iterdir()) == []
