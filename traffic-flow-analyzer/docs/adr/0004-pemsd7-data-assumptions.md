# ADR-0004 — PeMSD7 data assumptions

**Status:** accepted  
**Date:** 2026-10-17

## Context

The `ReplayFeed` / `fetch_pemsd7` / `load_pemsd7` stack uses the PeMSD7 dataset
distributed as part of the STGCN (Spatio-Temporal Graph Convolutional Networks,
IJCAI 2018) research code.  The file is a headerless CSV with no explicit units,
no column names, and no timestamps.  Several assumptions are required to turn it
into a usable speed time-series.

## Assumptions

### Source

PeMS (Caltrans Performance Measurement System), District 7 (Los Angeles area).
228 loop-detector sensors, sampled at 5-minute intervals over 44 weekdays during
May–June 2012.  Downloaded from the public GitHub mirror:
`VeritasYin/STGCN_IJCAI-18` (pinned by SHA-256 in `replay.py`).

### License

This is an academic benchmark dataset.  The STGCN repository carries an MIT
licence covering the *code*; the licence status of the data itself is not stated.
**Users must verify data-use terms before any commercial application.**  This
project accesses the data solely for research and educational purposes.

### Timestamps

The file has no timestamps.  They are *reconstructed* as follows:

- Start date: `2012-05-01` (Monday).
- Frequency: 5 minutes, 288 slots per day.
- Weekends are absent; `pd.bdate_range` is used to skip them.
- No timezone offset is applied; timestamps are treated as local time
  (Pacific time is implied by the Caltrans source, but no DST correction is
  applied).  All timestamps are naive (`tzinfo=None`).

Consequence: when the horizon of a forecast crosses a weekend boundary, the
corresponding `origins()` entry is excluded (the gap would break the contiguous-
horizon assumption in `ReplayFeed.origins`).

### Speed units

The CSV values are assumed to be **miles per hour (mph)**, consistent with the
PeMS convention.  They are multiplied by `1.609344` on load to produce km/h.
This is an assumption; the file header is absent and no unit metadata is present.

### Sensor subset

`load_pemsd7` loads all 228 sensors by default.  Passing `sensors=N` keeps an
evenly-spaced deterministic subset of N columns (rounded indices from
`np.linspace`).  The default for the CLI `evaluate` command is 8 sensors; the
full 228-sensor run is feasible but takes longer.

### Free-flow speed

`ReplayFeed` computes each sensor's free-flow speed as the 95th-percentile of
the first 7 recorded days (`WARMUP_DAYS`).  This is a heuristic; sensors with
unusual early-period traffic will have skewed free-flow estimates.

## Consequences

- Evaluation results are only meaningful for weekday traffic in the covered period.
- Any "incident" detected on weekend-adjacent data may reflect the gap rather
  than a real slowdown.
- Speed values, congestion indices, and all thresholds are in km/h throughout
  the codebase; conversion happens once, at load time.
- Timezone-naive comparison between `ReplayFeed` and `SyntheticFeed` timestamps
  is safe because both use naive datetimes.
