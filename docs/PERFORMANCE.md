# Performance

Targets from A6.1, what was measured, what was changed to get there, and what is still not met.
Every figure below is a **single run on one development laptop**, not an average and not a
guarantee; the JSON behind each is committed.

## Environment

Intel Core i9-9880H (8 cores, 16 threads), 16 GB RAM, macOS; Python 3.11.14; PostgreSQL 15 and
Redis 7 in Docker Desktop (8 GB, 16 CPUs allotted), with default PostgreSQL settings
(`shared_buffers` 128 MB, `work_mem` 4 MB) on the Docker virtual disk. A tuned server with local
NVMe storage should do better; Docker Desktop on macOS adds latency to every round trip, which is
what most of the work below was about.

## Method

- **Parsing and ingestion**: `python -m recon.cli.benchmark --scale N` (`make bench`). It builds the
  golden dataset at N times its high-volume classes (17 files, three formats, seven banks), measures
  each parser alone, then ingests every file through the real `IngestionPipeline` (parse, PAN masking,
  raw insert, normalisation, classification, anomaly scan, audit) and runs one `FULL` matching run per
  bank, inside a transaction that is rolled back.
- **Matching**: `python -m recon.bench.run --size N --seed 42 --set-based-exact`. Synthetic entities
  with known ground truth from a mix fixed in `recon/bench/dataset.py` before any result was seen
  (DD-19). It is one bank, so the unmatched pool is not split across banks as it would be in
  production, and the data was written by the engine's author (see `TESTING.md`).
- Results: `docs/benchmarks/pipeline/`, `docs/benchmarks/current/`. The older `baseline/` and
  `set-based-exact/` directories are the before-pictures and pre-date the changes below.

## Results against A6.1

| Target | Measured | Verdict |
|---|---|---|
| CSV parsing above 150,000 records/min | 1.6 to 3.5 million/min | **Met** |
| MT940 parsing above 50,000 records/min | 1.0 to 2.1 million/min | **Met** |
| CAMT.053 parsing above 30,000 records/min | 177,000 to 648,000/min | **Met** |
| Ingestion, all formats combined, 100,000 records/min | 98,000/min at 6,700 records; **118,000/min** at 33,100 records (all stages, one process) | **Met** at the larger size, marginal at the smaller (per-file overhead dominates a small batch) |
| Exact matching of 500,000 records in under 10 s | **34.6 s** for 498,200 records (180,200 pairs); 11.4 s for 188,000 | **Not met**: about 3.5 times too slow |
| Fuzzy matching of the remaining pool (15 to 30%) within 60 s | **16.1 s** at 188,000 records (pool about 28%); **75.9 s** at 498,200 records (pool about 28%) | **Met at 188,000, not met at 498,200** (1.3 times too slow) |
| Peak memory below 2 GB for 1 million records | **1.0 GB** peak resident for 498,200 records (whole matching run in one process) | **Not measured at 1 million**; linear extrapolation gives about 2 GB, i.e. at the limit |
| Database queries within the stated latency | Not measured separately | **Not assessed** |

Matching quality on the same data (one seed, one mix): no false auto-matches in 206,697
auto-matches at 498,200 records; 95.1% of true pairs auto-matched; 10,600 of 26,503 review-band
pairs were correct (the rest are the planted sequential-identifier decoys, which must not auto-match,
AE-37). These rates are set by the mix and are not comparable to the PDF's.

## Matching, by size (seconds, unprofiled)

| Records | Exact | Fuzzy | Whole run (orchestrator) | Seed insert |
|---|---|---|---|---|
| 37,600 (20,000 entities) | 2.5 | 2.9 | 6.1 | 10.6 |
| 188,000 (100,000 entities) | 11.4 | 16.1 | 31.6 | 52.5 |
| 498,200 (265,000 entities) | 34.6 | 75.9 | 123.3 | 146.5 |

For comparison, before this work at 20,000 entities the whole run took 33.4 s (set-based exact
2.3 to 2.9 s, then fuzzy about 30 s); the Python exact stage took 140.7 s. At 100,000 entities, after
the rejected-pair check was added and before it was fixed, exact took 95 s and fuzzy 183 s.

## What was found and changed

Each item is a profile first, then a change, then a re-measurement.

1. **Rejected-pair check planned as a per-pair scan (IB-17).** `NOT EXISTS` against `match_results`
   became a nested loop scanning the table once per candidate pair (10.4 million buffer hits at 20,400
   pairs). Now a hashed `NOT IN` over the partial index, with `ANALYZE match_results`. Exact at
   100,000 entities: 95 s to 13 s; the stage became linear in size.
2. **Fuzzy stage waited on the database (DD-34).** 85% of its time was round trips (about ten per
   match). It now chooses pairs in memory and writes 500 matches and their claims per savepoint with
   two multi-row inserts. 20,000 entities: 32.8 s to 2.9 s.
3. **ORM bookkeeping on bulk inserts.** Inserting rows as ORM objects cost about 0.8 ms each against
   0.08 ms through a Core multi-row insert (measured: 6,700 raw rows in 5.6 s versus 0.54 s). Raw
   rows, normalised rows, new counterparty statistics and the fuzzy matches now use Core inserts.
   End-to-end ingestion: 27,000 to 118,000 records/min.
4. **Per-counterparty statistics lookups.** One query per counterparty became one per file.
5. **Exact statement.** The status update joins on the partition key too, so each row is found in
   one monthly partition instead of probing all of them (statement at 30,000 entities: 2.8 s to 2.0 s).

## What is still slow, and why

- **Exact at 500,000 records (34.6 s).** One SQL statement groups the pool, pairs unambiguous keys,
  inserts the matches and claims and updates 360,000 rows in a partitioned table; the cost is
  mostly the writes (a match row with two JSONB columns, two claims, two status updates per pair) and
  the sort of the pool. Getting under 10 s would need a different write path (for example `COPY` into
  an unlogged staging table, or relaxing the per-row claim ledger for exact matches), which was
  judged not worth weakening the exclusivity guarantee for.
- **Fuzzy at 498,200 records (75.9 s).** Phase 1 (scoring) is single-threaded Python over about
  66,000 externals; the profile at 6,000 entities is now dominated by the two bulk inserts. It
  could be split across worker processes per amount bucket; it has not been.
- **Memory** is dominated by loading the unmatched pool as ORM objects for the fuzzy stage.
- **The benchmark runs all of it in one transaction on one connection**, so it cannot show
  concurrency effects.

## Reproducing

```bash
make migrate-test
export TEST_DATABASE_URL="postgresql+psycopg://recon_app:<password>@localhost:5432/recon_test"
make bench SCALE=100                                    # parsing, ingestion, per-bank runs
python -m recon.bench.run --size 100000 --seed 42 --set-based-exact --output /tmp/100k.json
pytest -q -m performance                                # parse throughput as a regression gate
```
