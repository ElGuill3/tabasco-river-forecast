# Experiment 16: per-station operational models

Pre-registered in L65; covers S1 and S3. No tuning or recalibration on scoring data.
Training targets: 1999-01-01–2022-12-31 for every station, avoiding the TEATB datum change.
Strict bounded SIH/reanalysis loaders are reused from experiment 15 with the 2022 boundary.
The perfect-forecast construction is unchanged: all forecast summaries equal target-day
reanalysis, with zero ensemble spread. Past discharge is shifted one day as in experiment 13.
L1 uses fit_l1 with issue-day stage (age 0); M1 uses the same unchanged conformal recipe
without stage. Scoring uses the 11-member CEMS summary, day-0 past discharge and obs_day_q
from that same day-0 archive, as in experiment 13 part B. No target is imputed.
Issues: 2023-08-01–2026-10-13, restricted to observed issue and target stages and complete features.
All four methods use identical scoring rows. Climatology is the day-of-year median of
observed training stages (±15-day window). MAE units are metres; coverage is inclusive q10–q90.
Skill = 1 − model MAE / persistence MAE (undefined for zero persistence MAE).
Accepted (L61) means strictly L1 q50 MAE < persistence MAE; M1 does not decide acceptance.
Acceptance is an experimental eligibility result, not deployment or a production-model change.

Status: **completed**. Scoring runs: 1; bug-forced reruns: 0.
Command: `timeout 5400 env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 uv run python -B experiments/16_station_models.py`. Elapsed: 1027.80 seconds.

## Extraction check

BDCTB: 1170 issues / 179010 rows compared, 2023-08-01–2026-10-13; maximum absolute difference 0 m³/s (tolerance 1e-9).
Extracted BDCTB lives only in `.cache/t24/BDCTB/operational/`; originals were not overwritten.
Open-Meteo cell longitudes are shifted +0.05° to the CEMS labels (same physical cell).
Extraction command: `timeout 3600 uv run python -B experiments/16_extract_operational.py`; elapsed 115.82 seconds.

## Limitations

- Training on a perfect forecast is expected to give bands that are too narrow with real forecasts.
- The scoring period holds only two to three wet seasons.
- GABTB has no valid IPCET reading yet (Gaviotas OCR); even an accepted lead cannot go live until the reader works.
- Issue-day CONAGUA stage simulates an IPCET reading available every day; actual publication gaps, reading age and OCR errors are not evaluated here.
- BDCTB is a reference only; its reforecast-trained production model is unchanged. Earlier BDCTB operational evaluations are not unseen evidence.
- Grijalva stations are regulated by Peñitas releases, which GloFAS may not represent (L61).

## Lead 1 day(s)

| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE | L1 skill | M1 skill | L1 coverage | M1 coverage | accepted (L61) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BDCTB | 661 | 0.194 | 0.684 | 0.245 | 1.008 | 0.209 | -1.796 | 0.834 | 0.817 | yes |
| PNVTB | 665 | 0.263 | 0.771 | 0.301 | 1.010 | 0.126 | -1.563 | 0.761 | 0.823 | yes |
| SAACP | 1111 | 0.344 | 0.674 | 0.403 | 1.059 | 0.145 | -0.673 | 0.850 | 0.833 | yes |
| TAPTB | 616 | 0.199 | 0.866 | 0.226 | 0.944 | 0.118 | -2.839 | 0.888 | 0.823 | yes |
| MACTB | 662 | 0.283 | 0.828 | 0.347 | 1.382 | 0.184 | -1.385 | 0.849 | 0.850 | yes |
| GABTB | 665 | 0.123 | 0.939 | 0.128 | 1.035 | 0.041 | -6.323 | 0.827 | 0.744 | yes |
| OXOTB | 650 | 0.192 | 0.298 | 0.137 | 0.337 | -0.401 | -1.175 | 0.566 | 0.478 | no |
| SJOCP | 1051 | 0.292 | 0.572 | 0.299 | 0.560 | 0.024 | -0.913 | 0.927 | 0.868 | yes |
| TEATB | 628 | 0.310 | 0.593 | 0.206 | 0.613 | -0.502 | -1.872 | 0.401 | 0.301 | no |
| PUYTB | 665 | 0.054 | 0.121 | 0.057 | 0.102 | 0.062 | -1.104 | 0.850 | 0.952 | yes |
| EPOTB | 552 | 0.161 | 0.506 | 0.168 | 0.717 | 0.038 | -2.020 | 0.913 | 0.889 | yes |
| GONTB | 667 | 0.094 | 1.536 | 0.067 | 1.508 | -0.407 | -21.983 | 0.831 | 0.580 | no |

## Lead 3 day(s)

| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE | L1 skill | M1 skill | L1 coverage | M1 coverage | accepted (L61) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BDCTB | 638 | 0.397 | 0.731 | 0.564 | 0.985 | 0.296 | -0.296 | 0.831 | 0.807 | yes |
| PNVTB | 642 | 0.448 | 0.775 | 0.610 | 0.992 | 0.265 | -0.271 | 0.799 | 0.829 | yes |
| SAACP | 1103 | 0.565 | 0.690 | 0.850 | 1.039 | 0.335 | 0.188 | 0.845 | 0.837 | yes |
| TAPTB | 589 | 0.292 | 0.826 | 0.326 | 0.917 | 0.103 | -1.536 | 0.883 | 0.837 | yes |
| MACTB | 637 | 0.574 | 0.859 | 0.877 | 1.345 | 0.346 | 0.022 | 0.857 | 0.862 | yes |
| GABTB | 643 | 0.223 | 0.951 | 0.272 | 1.038 | 0.181 | -2.494 | 0.855 | 0.737 | yes |
| OXOTB | 624 | 0.244 | 0.300 | 0.226 | 0.333 | -0.081 | -0.327 | 0.535 | 0.486 | no |
| SJOCP | 1041 | 0.437 | 0.595 | 0.502 | 0.561 | 0.130 | -0.185 | 0.886 | 0.835 | yes |
| TEATB | 602 | 0.370 | 0.594 | 0.312 | 0.616 | -0.186 | -0.903 | 0.362 | 0.297 | no |
| PUYTB | 642 | 0.075 | 0.113 | 0.086 | 0.100 | 0.134 | -0.307 | 0.874 | 0.960 | yes |
| EPOTB | 518 | 0.260 | 0.506 | 0.361 | 0.717 | 0.279 | -0.401 | 0.896 | 0.892 | yes |
| GONTB | 644 | 0.162 | 1.522 | 0.116 | 1.511 | -0.393 | -12.102 | 0.846 | 0.582 | no |

## Lead 7 day(s)

| Station | n | L1 MAE | M1 MAE | Persistence MAE | Climatology MAE | L1 skill | M1 skill | L1 coverage | M1 coverage | accepted (L61) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BDCTB | 651 | 0.631 | 0.798 | 0.885 | 0.999 | 0.288 | 0.099 | 0.793 | 0.819 | yes |
| PNVTB | 655 | 0.609 | 0.829 | 0.892 | 1.007 | 0.318 | 0.071 | 0.762 | 0.786 | yes |
| SAACP | 1103 | 0.749 | 0.789 | 1.190 | 1.037 | 0.370 | 0.337 | 0.814 | 0.807 | yes |
| TAPTB | 595 | 0.390 | 0.809 | 0.467 | 0.915 | 0.166 | -0.732 | 0.839 | 0.840 | yes |
| MACTB | 650 | 0.913 | 1.004 | 1.395 | 1.384 | 0.346 | 0.280 | 0.809 | 0.840 | yes |
| GABTB | 653 | 0.364 | 0.973 | 0.428 | 1.038 | 0.149 | -1.276 | 0.848 | 0.741 | yes |
| OXOTB | 635 | 0.262 | 0.302 | 0.300 | 0.336 | 0.128 | -0.005 | 0.537 | 0.485 | yes |
| SJOCP | 1037 | 0.485 | 0.614 | 0.598 | 0.556 | 0.189 | -0.026 | 0.862 | 0.851 | yes |
| TEATB | 608 | 0.443 | 0.595 | 0.387 | 0.618 | -0.144 | -0.536 | 0.334 | 0.296 | no |
| PUYTB | 655 | 0.101 | 0.121 | 0.110 | 0.104 | 0.086 | -0.099 | 0.904 | 0.948 | yes |
| EPOTB | 548 | 0.365 | 0.521 | 0.521 | 0.704 | 0.300 | -0.000 | 0.894 | 0.885 | yes |
| GONTB | 656 | 0.186 | 1.533 | 0.150 | 1.513 | -0.241 | -9.241 | 0.806 | 0.628 | no |

## Skipped station/leads

None.

## Observed periods

| Station | Lead | Training n | Training targets | Scoring issues | Scoring targets |
| --- | --- | --- | --- | --- | --- |
| BDCTB | 1 | 8173 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| BDCTB | 3 | 8106 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| BDCTB | 7 | 8083 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| PNVTB | 1 | 8299 | 1999-01-05–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| PNVTB | 3 | 8241 | 1999-01-07–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| PNVTB | 7 | 8217 | 1999-01-11–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| SAACP | 1 | 7827 | 1999-01-02–2022-12-31 | 2023-08-01–2026-10-13 | 2023-08-02–2026-10-14 |
| SAACP | 3 | 7745 | 1999-01-04–2022-12-31 | 2023-08-01–2026-10-13 | 2023-08-04–2026-10-16 |
| SAACP | 7 | 7709 | 1999-01-08–2022-12-31 | 2023-08-01–2026-10-11 | 2023-08-08–2026-10-18 |
| TAPTB | 1 | 7710 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| TAPTB | 3 | 7613 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| TAPTB | 7 | 7535 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| MACTB | 1 | 8118 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| MACTB | 3 | 8060 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| MACTB | 7 | 8022 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| GABTB | 1 | 7846 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| GABTB | 3 | 7712 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| GABTB | 7 | 7753 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| OXOTB | 1 | 7213 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| OXOTB | 3 | 7132 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| OXOTB | 7 | 7102 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| SJOCP | 1 | 3844 | 2004-05-05–2022-12-31 | 2023-08-21–2026-10-13 | 2023-08-22–2026-10-14 |
| SJOCP | 3 | 3786 | 2004-05-07–2022-12-31 | 2023-08-22–2026-10-13 | 2023-08-25–2026-10-16 |
| SJOCP | 7 | 3845 | 2004-05-11–2022-12-31 | 2023-08-21–2026-10-11 | 2023-08-28–2026-10-18 |
| TEATB | 1 | 8013 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| TEATB | 3 | 7943 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| TEATB | 7 | 7896 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| PUYTB | 1 | 6942 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| PUYTB | 3 | 6865 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| PUYTB | 7 | 6809 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
| EPOTB | 1 | 3734 | 2004-05-19–2022-12-31 | 2023-08-21–2025-09-02 | 2023-08-22–2025-09-03 |
| EPOTB | 3 | 3686 | 2004-05-21–2022-12-31 | 2023-08-21–2025-08-31 | 2023-08-24–2025-09-03 |
| EPOTB | 7 | 3736 | 2004-05-25–2022-12-31 | 2023-08-21–2025-08-27 | 2023-08-28–2025-09-03 |
| GONTB | 1 | 8172 | 1999-01-02–2022-12-31 | 2023-08-01–2025-09-02 | 2023-08-02–2025-09-03 |
| GONTB | 3 | 8113 | 1999-01-04–2022-12-31 | 2023-08-01–2025-08-31 | 2023-08-04–2025-09-03 |
| GONTB | 7 | 8080 | 1999-01-08–2022-12-31 | 2023-08-01–2025-08-27 | 2023-08-08–2025-09-03 |
