# Externally witnessed preregistrations

Seals in `active/` are produced by `research_kernel.forward_seal`. The three below were
sealed by `tools/look_ledger.py --seal-orphan`: an **orphan commit with a single file,
pushed alone** to `origin`, so the remote's timestamp attests the order and no local
`GIT_COMMITTER_DATE` can antedate it. They are listed here, not copied into `active/`,
because the kernel sealer refuses a window overlapping a burned period and the forward seal
starts two days inside the family's declared burn (see `PROJECT_TRUTH.md`).

| id | branch | commit | status |
|---|---|---|---|
| `SOURCE_SELECTION_RULE` | `prereg/source-selection-rule` | `e765081` | applied: Bitfinex refused (cond. 3), CME tested |
| `FORWARD_CROWD_POSITIONING_V1` | `prereg/forward-crowd-positioning-v1` | `0de75a9` | **active** — one look, not before 2028-12-06; pins in tag `prereg-forward-v1-code` |
| `CME_SEGMENTATION_V1` | `prereg/cme-segmentation-v1` | `0ddeee4` | **failed** t_net 1.577 < 1.960 — closed |

Verify a witness: `python3 tools/look_ledger.py --witness-orphan <file> <branch>`.
Verify the look chain: `python3 tools/look_ledger.py --verify --list`.
