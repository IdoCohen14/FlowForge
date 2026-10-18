# Local verification

Environment: Windows 11, Python 3.14.6, existing project virtual environment, Docker Desktop PostgreSQL 17 and Redis 7. Verification uses isolated generated databases and upload directories, preserving development data.

## Analyzer measurement

Command: `python scripts/benchmark.py --rows 100000 --runs 3`

Input: 100,000 rows, three columns, 1,779,024 bytes; deterministic generated product, price and quantity values.

| Run | Seconds |
| --- | ---: |
| 1 | 0.267863 |
| 2 | 0.259764 |
| 3 | 0.257260 |

Median: **0.259764 seconds**, approximately **384,965 rows/second** on this machine. Includes CSV parsing and file reads. Excludes validation at upload, HTTP, outbox polling, broker transit and database operations. Filesystem cache, host load and hardware affect results. This is a local analyzer measurement, not an end-to-end service throughput claim.

Run the script again to obtain measurements for your environment. No fixed latency assertions are used in tests.


## Integration checks

The real native pipeline completed the sales sample in 0.73 seconds in one local run; the complete Linux Compose stack completed it in 1.21 seconds. These are observations, not latency targets. Startup, queue polling and host load affect timings.

The isolated verifier confirmed duplicate delivery leaves results unchanged and that forcibly stopping a claimed worker recovers to completion on attempt two. The test advances only that generated database's lease deadline rather than waiting five minutes. PostgreSQL schema consistency was checked after applying all migrations. The application image built from the declared dependencies and the complete stack started against empty audit volumes.

The dashboard route is covered by an HTTP test. Browser visual inspection was unavailable because access to the local page was denied; no visual verification is claimed.

Final regression results: 152 tests passed on native Windows (19.32 seconds) and inside the Linux image (13.18 seconds), each with one existing Starlette TestClient deprecation warning. The real pipeline, duplicate delivery, and forced worker-crash recovery checks passed in both environments.
