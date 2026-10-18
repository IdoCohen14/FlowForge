"""Validate comma-separated UTF-8 files without loading the dataset into memory."""

import csv
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

MAX_COLUMNS = 256
MAX_FIELD_CHARACTERS = 64 * 1024


class CsvValidationError(ValueError):
    pass


def iter_validated_csv(path: Path) -> Iterator[list[str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            headers = next(reader, None)
            if headers is None:
                raise CsvValidationError("CSV must contain a header row")
            if not headers or len(headers) > MAX_COLUMNS:
                raise CsvValidationError(f"CSV must have between 1 and {MAX_COLUMNS} columns")
            names = [header.strip() for header in headers]
            if any(not name for name in names) or len(set(names)) != len(names):
                raise CsvValidationError("CSV headers must be nonempty and unique")
            if any(len(field) > MAX_FIELD_CHARACTERS for field in headers):
                raise CsvValidationError("CSV field exceeds the character limit")
            if any("\x00" in header for header in headers):
                raise CsvValidationError("CSV must not contain null characters")
            yield headers
            for row in reader:
                if len(row) != len(headers):
                    raise CsvValidationError("CSV rows must match the header column count")
                if any(len(field) > MAX_FIELD_CHARACTERS for field in row):
                    raise CsvValidationError("CSV field exceeds the character limit")
                if any("\x00" in field for field in row):
                    raise CsvValidationError("CSV must not contain null characters")
                yield row
    except UnicodeError:
        raise CsvValidationError("CSV must use UTF-8 encoding") from None
    except csv.Error:
        raise CsvValidationError("CSV structure is invalid or a field is too large") from None


def validate_csv(path: Path) -> None:
    for _ in iter_validated_csv(path):
        pass


def analyze_csv(path: Path) -> dict:
    """Stream statistics; decimal strings preserve precision in JSON results."""
    rows = iter_validated_csv(path)
    try:
        headers = next(rows)
        missing = [0] * len(headers)
        counts = [0] * len(headers)
        eligible = [True] * len(headers)
        totals = [Decimal(0) for _ in headers]
        minima: list[Decimal | None] = [None] * len(headers)
        maxima: list[Decimal | None] = [None] * len(headers)
        row_count = 0
        with localcontext() as context:
            context.prec = 50
            for row in rows:
                row_count += 1
                for index, raw in enumerate(row):
                    value = raw.strip()
                    if not value:
                        missing[index] += 1
                        continue
                    if not eligible[index]:
                        continue
                    try:
                        number = Decimal(value)
                    except InvalidOperation:
                        eligible[index] = False
                        continue
                    if not number.is_finite() or abs(number.adjusted()) > 10000:
                        eligible[index] = False
                        continue
                    counts[index] += 1
                    totals[index] += number
                    minima[index] = number if minima[index] is None else min(minima[index], number)
                    maxima[index] = number if maxima[index] is None else max(maxima[index], number)
            columns = []
            for index, header in enumerate(headers):
                numeric = None
                if eligible[index] and counts[index]:
                    numeric = {"count": counts[index], "minimum": str(minima[index]),
                               "maximum": str(maxima[index]),
                               "mean": str(totals[index] / counts[index])}
                columns.append({"name": header, "missing_values": missing[index], "numeric": numeric})
        return {"row_count": row_count, "column_count": len(headers), "headers": headers,
                "missing_values_total": sum(missing), "columns": columns}
    finally:
        rows.close()
