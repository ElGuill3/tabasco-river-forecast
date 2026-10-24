# Experiment 15: per-station perfect-GloFAS ceiling

Pre-registered in L63. This is an upper bound, not a real forecast,
publication decision or exclusion from T9e. The L61 acceptance rule is unchanged.
The test years 2017–2025 were not read: loaders stop at the date prefix
of the first later row, before reading its measurements. Only development
targets 1998–2016 are fitted/scored; four-year folds have a 14-day buffer.
Earlier observations are used only for issue-day inputs and discharge lags.
No target is imputed. Every comparator uses identical held-out rows.

L1-ceiling reuses fit_l1 with issue-day stage always revealed (age 0).
M1-ceiling has no stage input. Both reuse the unchanged conformal model:
three-year inner folds, 14-day buffer, existing hyperparameters.
Forecast control/mean/q10/q50/q90 all equal target-day reanalysis; spread is zero.
Past discharge is shifted one day as in experiment 13; obs_day_q uses the
same shifted series. Climatology is the existing training-fold day-of-year
median (±15-day window). Skill = 1 − model MAE / persistence MAE.
MAE is in metres; coverage is the fraction inside q10–q90 (nominal 0.80).
Zero persistence MAE gives undefined skill (n/a), not a substituted denominator.

Run status: **completed**. Scoring runs: 1; bug-forced reruns: 0.
Command: `timeout 3600 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 uv run python experiments/15_station_ceiling.py`.
Elapsed through last checkpoint: 3046.29 seconds.

## Lead 1 day

| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE | L1 skill | M1 skill | L1 coverage | M1 coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BDCTB | 6407 | 0.204 | 0.614 | 0.260 | 1.150 | 0.216 | -1.360 | 0.809 | 0.816 |
| PNVTB | 6504 | 0.249 | 0.731 | 0.295 | 1.120 | 0.158 | -1.475 | 0.807 | 0.825 |
| SAACP | 6015 | 1.011 | 1.384 | 1.747 | 1.803 | 0.422 | 0.208 | 0.812 | 0.817 |
| TAPTB | 6081 | 0.289 | 0.554 | 0.332 | 0.608 | 0.129 | -0.670 | 0.809 | 0.802 |
| MACTB | 6353 | 0.355 | 1.071 | 0.419 | 1.662 | 0.151 | -1.558 | 0.802 | 0.827 |
| GABTB | 6249 | 0.141 | 0.712 | 0.159 | 0.998 | 0.111 | -3.480 | 0.813 | 0.821 |
| OXOTB | 5548 | 0.265 | 0.329 | 0.429 | 0.407 | 0.381 | 0.233 | 0.813 | 0.811 |
| SJOCP | 1942 | 0.557 | 0.735 | 0.685 | 0.961 | 0.188 | -0.073 | 0.733 | 0.717 |
| TEATB | 6201 | 0.804 | 1.024 | 0.343 | 0.986 | -1.341 | -1.983 | 0.814 | 0.805 |
| PUYTB | 5130 | 0.169 | 0.331 | 0.201 | 0.337 | 0.159 | -0.649 | 0.799 | 0.753 |
| EPOTB | 1771 | 0.123 | 0.731 | 0.120 | 0.939 | -0.024 | -5.087 | 0.973 | 0.879 |
| GONTB | 6354 | 0.221 | 1.049 | 0.202 | 1.075 | -0.094 | -4.192 | 0.819 | 0.814 |

- TEATB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 1).
- EPOTB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 1).
- GONTB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 1).

## Lead 3 days

| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE | L1 skill | M1 skill | L1 coverage | M1 coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BDCTB | 6339 | 0.416 | 0.639 | 0.594 | 1.152 | 0.300 | -0.076 | 0.814 | 0.822 |
| PNVTB | 6447 | 0.429 | 0.739 | 0.611 | 1.121 | 0.297 | -0.210 | 0.810 | 0.824 |
| SAACP | 5928 | 1.242 | 1.401 | 2.220 | 1.816 | 0.440 | 0.369 | 0.810 | 0.819 |
| TAPTB | 6000 | 0.373 | 0.558 | 0.499 | 0.612 | 0.253 | -0.117 | 0.796 | 0.803 |
| MACTB | 6296 | 0.716 | 1.120 | 0.963 | 1.662 | 0.257 | -0.163 | 0.809 | 0.828 |
| GABTB | 6170 | 0.273 | 0.723 | 0.332 | 0.996 | 0.178 | -1.176 | 0.810 | 0.821 |
| OXOTB | 5476 | 0.310 | 0.335 | 0.534 | 0.412 | 0.419 | 0.373 | 0.816 | 0.808 |
| SJOCP | 1923 | 0.677 | 0.757 | 0.939 | 0.967 | 0.279 | 0.194 | 0.729 | 0.720 |
| TEATB | 6130 | 0.854 | 1.044 | 0.454 | 0.995 | -0.881 | -1.299 | 0.817 | 0.799 |
| PUYTB | 5052 | 0.202 | 0.323 | 0.254 | 0.327 | 0.205 | -0.269 | 0.786 | 0.766 |
| EPOTB | 1757 | 0.253 | 0.730 | 0.262 | 0.931 | 0.037 | -1.781 | 0.960 | 0.882 |
| GONTB | 6294 | 0.365 | 1.053 | 0.340 | 1.075 | -0.072 | -2.097 | 0.817 | 0.806 |

- TEATB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 3).
- GONTB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 3).

## Lead 7 days

| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE | L1 skill | M1 skill | L1 coverage | M1 coverage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BDCTB | 6312 | 0.570 | 0.678 | 0.938 | 1.153 | 0.392 | 0.278 | 0.819 | 0.822 |
| PNVTB | 6419 | 0.527 | 0.735 | 0.856 | 1.120 | 0.385 | 0.142 | 0.816 | 0.821 |
| SAACP | 5885 | 1.354 | 1.420 | 2.555 | 1.820 | 0.470 | 0.444 | 0.818 | 0.815 |
| TAPTB | 5938 | 0.409 | 0.560 | 0.596 | 0.612 | 0.313 | 0.060 | 0.803 | 0.800 |
| MACTB | 6260 | 0.959 | 1.147 | 1.511 | 1.663 | 0.365 | 0.241 | 0.816 | 0.827 |
| GABTB | 6129 | 0.377 | 0.736 | 0.513 | 0.992 | 0.265 | -0.435 | 0.808 | 0.826 |
| OXOTB | 5437 | 0.325 | 0.338 | 0.586 | 0.410 | 0.445 | 0.424 | 0.810 | 0.809 |
| SJOCP | 1921 | 0.728 | 0.762 | 1.057 | 0.975 | 0.312 | 0.279 | 0.739 | 0.727 |
| TEATB | 6083 | 0.887 | 1.065 | 0.523 | 1.005 | -0.696 | -1.036 | 0.813 | 0.809 |
| PUYTB | 4995 | 0.235 | 0.327 | 0.290 | 0.328 | 0.190 | -0.128 | 0.759 | 0.760 |
| EPOTB | 1763 | 0.374 | 0.748 | 0.412 | 0.936 | 0.093 | -0.815 | 0.941 | 0.884 |
| GONTB | 6260 | 0.427 | 1.054 | 0.402 | 1.070 | -0.062 | -1.619 | 0.813 | 0.810 |

- TEATB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 7).
- GONTB: unlikely to beat persistence at that lead even with perfect GloFAS (lead 7).

## Skipped station/leads

None.
