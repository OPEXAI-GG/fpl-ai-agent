# Company source baseline — 2026-10-28 (FAA-27)

Captured by Zaza (`zaza-mvsc3unw`) from 2026-10-28T19:57:01Z to 19:57:26Z (UTC). This is a
read-only inventory, **not an atomic snapshot**: files were hashed one after another while other
employees could be editing. Full detail: `manifest.json` in this folder.

## Identity
- Branch `task/phase1-dashboard`, HEAD `c6a0c5f6c486b8e61c582c0e03c3ba1a8316a6e8`, which is the same
  HEAD as the DB08 gate and M00.
- Working tree: 26 modified tracked files and 47 untracked top-level entries (paths at git's normal
  untracked granularity). Nothing is committed beyond HEAD.

## Source fingerprint (gate method)
The method is the same as `artifacts/db-hardening-20261023T143050Z/r3-closure/fingerprint.py`: SHA-256
over every file in `src/`, `tests/` and `rules/`, excluding `__pycache__`.
- Current: `a9cdb9e869d62ae9dce214ca520d132c95ccbf515c4647147114f2e2497075c7` (161 files).
  It was the same at the start and the end of the capture.
- Accepted DB08 gate: `875fa1a5…1e33e` (155 files). **It does not match.** Since the gate:
  - **Added (6):** `db/link_audit.py`, `db/link_candidates.py`, `db/player_links.py` and their
    three tests. These are the FAA-3 link work (2026-10-26), which Wegz accepted with the evidence
    in `docs/research/database-link-audit.md` and `artifacts/db-links-audit-20261026/r2/`.
  - **Changed (8):** `cli.py`, `db/database.py`, `db/quality.py`, `db/schema.py`,
    `model/project.py`, `tests/test_quality.py` (file times 2026-10-26, which matches the FAA-3
    period), plus `model/features.py` and `model/players.py`.
- Compared with the M00 source identity (`artifacts/model-deadline-20261027T1634Z/source-identity.json`,
  captured 2026-10-27 16:38Z), only **`model/features.py` and `model/players.py`** differ. Their
  file times are 16:46Z and 16:50Z on 2026-10-27, after M00. **No recorded review or gate covers
  these two files.** They look like work toward M02, but I have not confirmed that. A reviewer
  should treat them as unreviewed.

## Preserved references (data hashes recomputed and matching)
| Item | Value | Status |
| --- | --- | --- |
| M00 work copy `artifacts/db-links-20261026/work.duckdb` | `94f68833…a813` | recomputed, matches |
| `data_cache/football.duckdb` (production hash recorded in the r5 review) | `9bc925c1…5e1d` | recomputed, matches; it has not changed |
| DB08 review | `artifacts/codex-r5-review-20261026/review.md`; gate logs in `…/r5-gate/` | 814 passed, 5 skipped, 93% coverage (recorded, **not rerun**) |
| FAA-3 link audit | `docs/research/database-link-audit.md`, `artifacts/db-links-audit-20261026/r2/` | referenced only |
| M00 rate baseline | `ff69d7b0…13f5` | recorded, not recomputed |

## Limitations
- **Concurrent edit seen:** `artifacts/customer-discovery-review.md` appeared during the 25-second
  capture (another employee's work). It is not in the fingerprints.
- **Runtime state not fingerprinted:** `hive/`, `palace/`, `palace.lock`, `palace.rebuilt-056`,
  `roster.json` and `roster-backups/` change continuously. They are listed but not hashed.
- **Folders I cannot read:** git reported 5 directories with permission denied (old Codex review temp
  folders). Another 5 temp subfolders inside `codex-followup-20261024`, `codex-r3/r4/r5-review` could
  not be read. Those folder hashes therefore exclude them, and the skipped paths are listed in the
  manifest.
- Untracked folders are summarised as a file count, byte total and tree hash, not file by file.
- No tests, builds or permission checks were run. Nothing was committed, checked out, stashed or reset,
  and no config was changed. The only files written are these two deliverables.
