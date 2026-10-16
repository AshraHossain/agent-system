# Evaluation on real data: PeMSD7 replay

Out-of-sample rolling-origin evaluation of the forecaster on real recorded speeds, against
three baselines. **Summary: the model beats persistence only at the 60-minute horizon; it is
worse at 15 minutes, ties at 30, and its "90%" interval covers only about 59-66% of outcomes.**
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
* **No tuning on this data.** The forecaster constants (persistence 0.92/step, 1.645 interval
  multiplier) were fixed during development on the simulated feed, before any real data was
  loaded. This is a single deterministic run (no randomness).
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
