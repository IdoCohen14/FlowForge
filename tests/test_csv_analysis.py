from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy import event
from sqlalchemy.exc import DBAPIError

import tasks
from csv_analysis import CsvValidationError, analyze_csv
from models import CsvAnalysis, Job


def test_sales_sample_statistics() -> None:
    result = analyze_csv(Path(__file__).resolve().parents[1] / "sample_data/sales.csv")
    assert result["row_count"] == 4
    assert result["column_count"] == 3
    assert result["headers"] == ["Product", "Price", "Quantity"]
    assert result["missing_values_total"] == 0
    assert result["columns"][0]["numeric"] is None
    for column, minimum, maximum, mean in [(result["columns"][1], "25", "1200", "401.25"), (result["columns"][2], "2", "10", "5")]:
        assert column["numeric"] == {"count": 4, "minimum": minimum, "maximum": maximum, "mean": mean}


def test_missing_and_numeric_eligibility(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    path.write_text("decimal,mixed,empty,nonfinite\n0.1,1, ,NaN\n0.2,text,,2\n ,3, ,Infinity\n", encoding="utf-8")
    result = analyze_csv(path)
    assert result["missing_values_total"] == 4
    assert [column["missing_values"] for column in result["columns"]] == [1, 0, 3, 0]
    assert result["columns"][0]["numeric"] == {"count": 2, "minimum": "0.1", "maximum": "0.2", "mean": "0.15"}
    assert all(column["numeric"] is None for column in result["columns"][1:])


@pytest.mark.parametrize("content", ["a,b\n1\n", 'a\n"unfinished', "a,a\n1,2\n"])
def test_analysis_revalidates_structure(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CsvValidationError):
        analyze_csv(path)


@pytest.mark.parametrize("failure", [None, "malformed", "missing-file", "unsafe-path"])
def test_csv_worker_and_duplicates(test_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    filename = uuid4().hex + ".csv"
    if failure != "missing-file":
        (tmp_path / filename).write_text("a,b\n1\n" if failure == "malformed" else "amount\n0.1\n0.2\n", encoding="utf-8")
    with Session(test_engine) as session, session.begin():
        job = Job(name="CSV", job_type="csv")
        job.csv_analysis = CsvAnalysis(storage_filename="../private.csv" if failure == "unsafe-path" else filename)
        session.add(job)
        session.flush()
        job_id = job.id
    if failure:
        with pytest.raises(Exception):
            tasks.process_job.apply(args=[str(job_id)], throw=True)
    else:
        tasks.process_job.apply(args=[str(job_id)], throw=True)
    with Session(test_engine) as session:
        job = session.get(Job, job_id)
        assert job.status == ("failed" if failure else "completed")
        analysis = job.csv_analysis
        if failure:
            assert analysis.results is None
            assert analysis.error_message
            assert str(tmp_path) not in analysis.error_message
            assert "private.csv" not in analysis.error_message
        else:
            assert Decimal(analysis.results["columns"][0]["numeric"]["mean"]) == Decimal("0.15")
            assert analysis.error_message is None
        previous = analysis.results
    operation = Mock(side_effect=AssertionError("Duplicate executed"))
    monkeypatch.setattr(tasks, "analyze_csv", operation)
    tasks.process_job.apply(args=[str(job_id)], throw=True)
    operation.assert_not_called()
    with Session(test_engine) as session:
        assert session.get(CsvAnalysis, job_id).results == previous


def test_csv_result_and_completion_roll_back_together(test_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "get_application_engine", lambda: test_engine)
    filename = uuid4().hex + ".csv"
    (tmp_path / filename).write_text("a\n1\n", encoding="utf-8")
    with Session(test_engine) as session, session.begin():
        job = Job(name="CSV", job_type="csv")
        job.csv_analysis = CsvAnalysis(storage_filename=filename)
        session.add(job)
        session.flush()
        job_id = job.id

    def fail_result_update(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE csv_analyses"):
            connection.exec_driver_sql("SELECT 1 / 0")

    event.listen(test_engine, "before_cursor_execute", fail_result_update)
    try:
        with pytest.raises(DBAPIError):
            tasks.process_job.apply(args=[str(job_id)], throw=True)
    finally:
        event.remove(test_engine, "before_cursor_execute", fail_result_update)
    with Session(test_engine) as session:
        assert session.get(Job, job_id).status == "running"
        assert session.get(CsvAnalysis, job_id).results is None


def test_extreme_finite_exponents_are_not_numeric(tmp_path):
    from csv_analysis import analyze_csv
    path = tmp_path / "extreme.csv"
    path.write_text("a,b\n1e999999999,1e-999999999\n", encoding="utf-8")
    result = analyze_csv(path)
    assert all(column["numeric"] is None for column in result["columns"])
