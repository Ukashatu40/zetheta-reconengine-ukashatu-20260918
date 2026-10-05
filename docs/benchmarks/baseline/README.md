# Baseline benchmark, before any optimisation

Commands: `python -m recon.bench.run --size {1000,5000,20000} --seed 42 --output docs/benchmarks/baseline/size-N-seed-42.json`

Environment: Apple laptop (up to 16 threads), Python 3.11.14, PostgreSQL 15 in Docker Compose (Docker Desktop, not a tuned server), database `recon_test`. CPU model, RAM and Docker resource limits: (fill in).

Dataset: synthetic, deterministic, scenario mix fixed in `recon/bench/dataset.py` before any result was seen (DD-19). Rates are determined by that mix and are not comparable to the PDF's targets. Timings are single runs, not averages.
