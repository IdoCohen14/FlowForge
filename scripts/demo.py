"""Upload the sample over HTTP and verify real background processing."""
import argparse
import json
from pathlib import Path
import time
import httpx

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    start = time.monotonic()
    with httpx.Client(base_url=args.url, timeout=20) as client:
        while True:
            try:
                if client.get("/health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic()-start >= args.timeout:
                raise TimeoutError("API did not become ready")
            time.sleep(.5)
        with (Path(__file__).resolve().parents[1]/"sample_data/sales.csv").open("rb") as source:
            response = client.post("/jobs/csv", files={"file": ("sales.csv", source)}, data={"name": "Sales sample analysis"})
        response.raise_for_status()
        job = response.json()
        print("Created job", job["id"])
        while time.monotonic()-start < args.timeout:
            response = client.get(f"/jobs/{job['id']}/analysis")
            response.raise_for_status()
            result = response.json()
            if result["status"] == "failed":
                raise RuntimeError(result["error_message"])
            if result["status"] == "completed":
                data = result["results"]
                assert (data["row_count"], data["column_count"], data["missing_values_total"]) == (4,3,0)
                assert data["columns"][1]["numeric"]["mean"] == "401.25"
                assert data["columns"][2]["numeric"]["mean"] == "5"
                print(json.dumps(result, indent=2))
                print(f"Verified upload -> dispatcher -> Redis -> Celery -> PostgreSQL -> results API in {time.monotonic()-start:.2f}s")
                return
            time.sleep(.5)
    raise TimeoutError("Job did not complete; check worker and dispatcher logs")

if __name__ == "__main__":
    main()
