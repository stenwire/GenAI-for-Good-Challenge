# Submissions

One row per `Submission.md` produced for Zindi. The user uploads; the leaderboard column is
filled in when the score is reported. Tags `sub-NN-YYYY-MM-DD` point at the commit that produced
the file (`git checkout <tag>` reproduces it).

| Date | Git tag | Run ID | Dev score (run) | File size | SHA256 | Leaderboard | Notes |
|---|---|---|---|---|---|---|---|
| 2026-11-25 | `sub-01-2026-11-25` (`f27f59e`) | `20261125-2323-f27f59e` | 0.0975 (`20261125-2322-f27f59e`, 62 dev questions) | 795,356 bytes | `e41ef0d2d5097cec0613dfbc66ebabcae91617263c11f4f058b4b266b86459f9` | | M1 baseline: BM25 + one compose call per question; max 1 call / 600 tokens. Above the unofficial 256 KiB guidance; a file that fails processing does not count against limits (brief §4.3). |
