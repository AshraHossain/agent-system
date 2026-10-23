# ADR-0005 — PEMS08 data assumptions

**Status:** accepted
**Date:** 2026-10-23

## Context

PeMSD7 has weekdays only, so weekend behaviour could not be evaluated. PEMS08 is a second
public PeMS benchmark, distributed with the ASTGCN research code, that covers every day of
its period. It is used only for evaluation and replay; constants were never tuned on it.

## Assumptions

### Source

Caltrans PeMS, District 8 (San Bernardino / Riverside), 170 loop detectors, 5-minute
aggregates, July-August 2016. Downloaded from the public GitHub mirror
`Davidham3/ASTGCN` (`data/PEMS08/pems08.npz`), pinned by SHA-256 in `replay.py`. The file is
read with `allow_pickle=False`.

### Licence

As with PeMSD7, the code repository's licence does not state the data's terms. **The owner
must verify data-use terms before any redistribution or commercial use.** The file is never
committed (`var/` is gitignored).

### Layout and timestamps

`data` has shape `(17856, 170, 3)`: 62 days × 288 slots, sensors, and the channels flow,
occupancy and speed. The file has no timestamps; they are *reconstructed* as 62 consecutive
days from `2016-07-01 00:00` at 5-minute steps (naive local time, no DST handling). The
length matches July + August exactly, which supports but does not prove this.

### Speed units

Channel 2 is assumed to be speed in **mph** (values 3-82, mean ~64, consistent with PeMS
freeway speeds in mph) and is converted to km/h on load.

### Sensors and free flow

Columns are named `P000`..`P169` by position; sensor station IDs are not in the file, so
there are no coordinates for the map. Subsetting and free-flow speed work as for PeMSD7
(evenly spaced subset; 95th percentile of the first 7 days).

## Consequences

- `evaluate --period weekend` is meaningful on PEMS08 (and empty on PeMSD7).
- PEMS08 results are an out-of-dataset test of constants chosen on PeMSD7.
- Replayed PEMS08 sensors still have no real coordinates.
