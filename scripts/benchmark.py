"""Reproducible local analyzer benchmark; not a distributed throughput claim."""
import argparse
import csv
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from csv_analysis import analyze_csv

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100000)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.rows < 1 or args.runs < 1:
        parser.error("rows and runs must be positive")
    with tempfile.TemporaryDirectory(prefix="flowforge_benchmark_") as directory:
        path = Path(directory)/"generated.csv"
        with path.open("w", newline="", encoding="utf-8") as target:
            writer = csv.writer(target)
            writer.writerow(["product", "price", "quantity"])
            for i in range(args.rows):
                writer.writerow([f"item-{i%100}", f"{i%1000}.25", i%10])
        durations = []
        for _ in range(args.runs):
            start = time.perf_counter()
            result = analyze_csv(path)
            durations.append(time.perf_counter()-start)
            assert result["row_count"] == args.rows
        print(json.dumps({"python": platform.python_version(), "platform": platform.platform(),
            "rows": args.rows, "bytes": path.stat().st_size, "runs_seconds": durations,
            "median_seconds": statistics.median(durations),
            "rows_per_second": args.rows/statistics.median(durations)}, indent=2))

if __name__ == "__main__":
    main()
