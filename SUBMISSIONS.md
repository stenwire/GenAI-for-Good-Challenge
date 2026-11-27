# Submissions

One row per `Submission.md` produced for Zindi. The user uploads; the leaderboard column is
filled in when the score is reported. Tags `sub-NN-YYYY-MM-DD` point at the commit that produced
the file (`git checkout <tag>` reproduces it).

| Date | Git tag | Run ID | Dev score (run) | File size | SHA256 | Leaderboard | Notes |
|---|---|---|---|---|---|---|---|
| 2026-11-25 | `sub-01-2026-11-25` (`f27f59e`) | `20261125-2323-f27f59e` | 0.0975 (`20261125-2322-f27f59e`, 62 dev questions) | 795,356 bytes | `e41ef0d2d5097cec0613dfbc66ebabcae91617263c11f4f058b4b266b86459f9` | 0.215775462 public (Zindi `69RjT4Yb`, uploaded 2026-11-26; rank 98, team Theus) | Accepted at 795,356 bytes. M1 baseline: BM25 + one compose call per question; max 1 call / 600 tokens. Above the unofficial 256 KiB guidance; a file that fails processing does not count against limits (brief §4.3). |
| 2026-11-27 | `sub-02-2026-11-27` (`0d6258e`) | `20261127-113054-0d6258e` | 0.5517 (`20261127-110258-4ef62f9`, 92 dev questions) | 990,785 bytes | `b674a39f899730c485fb439c07d604d6945f0c4f41e1fcdf32c5b9e2a192c752` |  | M2 planner pipeline: hybrid retrieval, route (model + rules), code-built plans for common shapes (121 of 600 Test questions) or a JSON-schema model plan with repair, compose from computed facts; max 5 calls / 1,295 tokens per question; 0 blank, 0 fallback. Above the unofficial 256 KiB guidance (the 795,356-byte M1 file was accepted); size control is T-306. |
