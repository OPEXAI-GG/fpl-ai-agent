# FAA-3 audit evidence (Wegz, 2026-10-26)
Supports docs/research/database-link-audit.md. Nothing here changes implementation or original databases.
- test_repro.py: synthetic reproductions R1-R7. Each asserts the CURRENT defective behaviour, so after a fix the matching test should FAIL. Copy it into a repo copy (any folder at the repo root), then run `uv run python -m pytest <dir>/test_repro.py -q --no-cov -p no:cacheprovider --basetemp=<scratch>`; it imports tests.test_link_candidates. At audit time: 7 passed.
- mutate.py: mutation harness. `python -I mutate.py <repo-copy>` applies 18 single-line mutants one at a time, runs the three link test files, then restores and checks each file. Never point it at the working repo. mutations.log: 11 killed, 7 survived (M2, M3, M5, M8, M13, M14, M15).
- probe.py: read-only queries on a linked DB copy (`.venv/Scripts/python.exe -I probe.py <copy.duckdb>`).
- full-suite.log: full CI command on a copy lacking .github/ and scripts/ (793 passed, 13 failed, coverage 92.80%). workflows-rerun.log: the 13 failing tests' files rerun with those folders present.
- lint-clean-copy.log: ruff on an unmodified copy (src identical to the repo). lint-scratch-with-repro.log: ruff on the copy that also held test_repro.py; its only complaint is that file.
