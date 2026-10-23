# Evaluation on real data: PeMSD7 replay

Out-of-sample rolling-origin evaluation of the forecaster on real recorded speeds, against
three baselines. **Current summary (held-out split): the model beats persistence at every
horizon (15 min: 4.69 vs 4.92 km/h MAE) and its "90%" interval covers 89.7-90.2% of
outcomes.** See [Current results](#current-results-held-out-split). The sections after it are
earlier runs, kept for comparison.

## Current results: held-out split

`--split` keeps only the second half of the origins (278 of 555). The two constants chosen
on data, `LAST_WEIGHT = 0.9` (blend toward the latest reading) and `INTERVAL_TAIL = 0.035`
(leave-one-day-out conformal interval), were picked on the **first** half only, so nothing
reported here was seen while tuning. 38 sensors; same setup as below otherwise.

```bash
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv --sensors 38 --split   # ~2-8 min
```

| Horizon | Model MAE | Persistence | Seasonal naive | Historical avg | Coverage | Width (km/h) |
|---|---|---|---|---|---|---|
| 15 min | **4.69** | 4.92 | 9.75 | 8.41 | 0.898 | 22.1 |
| 30 min | **6.37** | 7.23 | 9.99 | 8.59 | 0.897 | 31.9 |
| 60 min | **7.71** | 11.02 | 9.88 | 8.46 | 0.902 | 40.5 |

By period (MAE model / persistence; coverage): peak 15 min 4.88 / 5.12, 0.886; off-peak
15 min 4.47 / 4.68, 0.912; peak 60 min 8.06 / 11.38, 0.888; off-peak 60 min 7.31 / 10.60, 0.918.

* **Short horizon fixed.** Blending toward the latest reading (weight `0.9**h`, residual from
  the last reading only) moved 15-minute MAE from 5.87 to 4.69, now below persistence.
  Training-half MAE was 4.59 vs 4.80, so the held-out gain is consistent, not a tuning artefact.
  At 60 minutes the model is unchanged (7.66 → 7.71).
* **Interval calibrated, but wide.** Coverage matches the nominal 90% overall; peak hours are
  slightly under (~88.7%) and off-peak slightly over (~91%). The width (22-41 km/h) is about
  what the previous band had: on this data a band that honestly covers 90% is that wide,
  because errors are heavy-tailed (the 5% tail level gave only ~85% at 17-34 km/h).
* **Previous held-out run** (before the blend and the new interval): model 5.87 / 7.05 / 7.66
  MAE, coverage 0.86-0.89 at 24-41 km/h.
* The all-228-sensor held-out run has not been recorded yet.

## Original pre-calibration run (all origins)

The earlier simulated-data result (model MAE 1.52 vs persistence 3.12 km/h at 60 min) does
not transfer to real data in the same way.

## Setup

* **Data:** PeMSD7 (Caltrans PeMS, May-June 2012): 228 sensors, 44 weekdays, 5-minute speeds
  (12,672 rows). Fetched with `trafficflow fetch-data` from a public research mirror; the
  archive is pinned by SHA-256. The data is **not** committed to this repo, and I have not
  verified PeMS redistribution terms.
* **Reconstructed, not recorded:** the file has no header or timestamps. Timestamps assume the
  44 weekdays starting 2012-05-01 at 288 slots/day, and speeds are assumed to be mph
  (converted to km/h). If either assumption is wrong, absolute numbers change.
* **Sensors:** 38 of 228, evenly spaced by column index (`--sensors 38`).
* **Origins:** hourly, 06:00-20:00, on every weekday after a 7-day warm-up
  (**555 origins**, 21090 forecasts per horizon). Horizons crossing a weekend gap are excluded.
* **Metric:** error at the forecast step exactly *h* minutes ahead (km/h; MAPE in %).
  *Peak* = weekday origins at hours 6-9 and 15-18; *off-peak* = the rest.
* **No tuning on this data (this original run).** The forecaster constants (persistence
  0.92/step, 1.645 interval multiplier) were fixed during development on the simulated feed,
  before any real data was loaded. This is a single deterministic run (no randomness). The
  current results above add two constants tuned on the first half of origins only.
* **Free-flow speed** per sensor = 95th percentile of its first 7 recorded days.

Reproduce:

```bash
uv run trafficflow fetch-data --dest var/data
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv --sensors 38   # ~2.5 min
```

## Results: MAE (km/h), all origins

| Method | 15 min | 30 min | 60 min |
|---|---|---|---|
| Model (seasonal + residual) | 5.86 | 7.11 | 7.91 |
| Persistence | 4.86 | 7.11 | 10.83 |
| Seasonal naive | 10.69 | 10.84 | 10.79 |
| Historical average | 8.89 | 9.03 | 8.92 |

## Results by period: MAE (km/h) / MAPE (%)

| Horizon | Period | Model (seasonal + residual) | Persistence | Seasonal naive | Historical average |
|---|---|---|---|---|---|
| 15 min | peak | 6.16 / 11.0 | 5.17 / 8.7 | 11.55 / 21.3 | 9.65 / 18.7 |
| 15 min | offpeak | 5.51 / 8.1 | 4.50 / 5.8 | 9.71 / 14.0 | 8.03 / 12.3 |
| 30 min | peak | 7.57 / 13.7 | 7.71 / 13.7 | 11.83 / 21.9 | 9.92 / 19.2 |
| 30 min | offpeak | 6.58 / 9.7 | 6.43 / 8.5 | 9.70 / 13.7 | 8.02 / 11.9 |
| 60 min | peak | 8.65 / 15.7 | 11.65 / 20.8 | 11.98 / 21.7 | 9.98 / 19.0 |
| 60 min | offpeak | 7.07 / 11.1 | 9.90 / 14.6 | 9.42 / 13.9 | 7.72 / 12.1 |

## Interval coverage (nominal 90%)

| Horizon | All | Peak | Off-peak | Mean width (km/h) |
|---|---|---|---|---|
| 15 min | 0.639 | 0.619 | 0.662 | 9.7 |
| 30 min | 0.611 | 0.589 | 0.636 | 10.3 |
| 60 min | 0.627 | 0.601 | 0.656 | 11.5 |

## What this says, and what it does not

* At **15 minutes** the latest reading is a better forecast than the model (5.86 vs 4.86 MAE).
  At **30 minutes** they tie. At **60 minutes** the model clearly wins (7.91 vs 10.83), and it
  also beats the historical average (8.92). Seasonal naive (yesterday's value) is worst.
* The interval is **overconfident**: it was designed for 90% and achieves roughly 59-66%.
  Do not read the shaded band on the dashboard as a calibrated 90% interval.
* Hypotheses for the short-horizon weakness and the narrow interval (**not tested**): the
  residual correction averages the last three readings and so lags; the interval width comes
  from in-sample residuals around the median profile and ignores horizon-dependent error.
* This is one dataset, one city and period (2012 California freeways, weekdays only), a
  38-sensor subset, and reconstructed timestamps. It does not show the model works on other
  networks, on weekends, or live.
* Next steps are in [TASK.md](../TASK.md): calibrate the interval from history, improve the
  short-horizon forecast **using a train/test split** (tune on early days, report on later
  days), and add more forecasters.

## Scaling evaluation

To evaluate on all 228 sensors instead of the default 38:

```bash
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv --all-sensors   # ~30s on a laptop
```

Results will vary substantially by sensor — high-variance urban sensors show larger MAE
than smooth freeway segments. The 8- and 38-sensor subsets (evenly spaced by column index)
give a representative sample; the full run reveals the spread.

To evaluate on a different hour range:

```bash
uv run trafficflow evaluate var/data/PeMSD7_V_228.csv --from-hour 6 --to-hour 9  # peak only
```

PeMSD7 contains weekday data only; `--period weekday` (default) and `--period all` produce
identical results for this dataset. The `--period all` flag is wired in for future feeds
that include weekend observations.

Suggested workflow:
1. Quick sanity check: `--sensors 8` (~5 s).
2. Representative run: `--sensors 38` (~2.5 min, as used for the table above).
3. Full picture: `--all-sensors` (~30 s per commit if parallelised, but single-threaded here).
