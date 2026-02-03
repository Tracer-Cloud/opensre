## Unused Code Cleanup Plan

- [x] Scan repo for unused code indicators (ruff, references)
- [x] Remove unused imports/variables and dead files
- [x] Verify with ruff (and targeted tests if needed)
- [x] Record results and any follow-ups

## Results

- Ruff unused checks found no issues.
- Removed unused `data-alloy/` directory (seed + remotecfg).
- `python3 -m ruff check .` passed.
