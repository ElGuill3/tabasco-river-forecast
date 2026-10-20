# T19 — live GloFAS source (S5, S8)

**L45 verdict: adopted. Live source: Open-Meteo.**

## Reproduction and provenance

Run `timeout 900 uv run python experiments/14_live_source.py`. All inputs are local; the script makes no HTTP requests. SHA-256 hashes are in the JSON. Open-Meteo: GitHub run 39339164931, fetched 2026-10-19T23:03:54Z; 37 dates 2026-10-12–2026-11-17, 51 members. The separate control response equals member 0. Retain `.cache/t19/om/` and `.cache/t19/raw/` (ignored files).

## Alignment established before scoring

Control trajectory for Oct 19 follows OM on Oct 20-26, including the Oct 21 peak. Oct 18 only matches its lead-1 archived value on Oct 19; its later control and perturbed trajectories are different. OM past_days does not retain Oct 18 leads 2-7. Therefore only Oct 19 is honestly matched. The API has no issue timestamp: identification uses trajectory fingerprints, not the adoption share. No issue or member permutation maximizes L45.

Matched run: **2026-10-19 00 UTC**, leads 1–7 = valid dates October 20–26. CEMS valid = issue + lead; Open-Meteo dates equal CEMS valid dates, without a shift. CEMS 17.425/268.525 is OM 17.425/-91.525 per the documented cell-label offset.

| CEMS issue | lead | valid/OM date | CEMS control m³/s | OM control m³/s |
|---|---:|---|---:|---:|
| 2026-10-18 | 1 | 2026-10-19 | 3713.46875 | 3714.35 |
| 2026-10-18 | 2 | 2026-10-20 | 4900.640625 | 4284.49 |
| 2026-10-18 | 3 | 2026-10-21 | 5519.0859375 | 4807.39 |
| 2026-10-18 | 4 | 2026-10-22 | 5330.7578125 | 4622.81 |
| 2026-10-18 | 5 | 2026-10-23 | 4931.7578125 | 4354.12 |
| 2026-10-18 | 6 | 2026-10-24 | 4600.28125 | 4119.98 |
| 2026-10-18 | 7 | 2026-10-25 | 4249.015625 | 3809.66 |
| 2026-10-19 | 1 | 2026-10-20 | 4283.484375 | 4284.49 |
| 2026-10-19 | 2 | 2026-10-21 | 4804.46875 | 4807.39 |
| 2026-10-19 | 3 | 2026-10-22 | 4627.6328125 | 4622.81 |
| 2026-10-19 | 4 | 2026-10-23 | 4350.6484375 | 4354.12 |
| 2026-10-19 | 5 | 2026-10-24 | 4122.3671875 | 4119.98 |
| 2026-10-19 | 6 | 2026-10-25 | 3810.3046875 | 3809.66 |
| 2026-10-19 | 7 | 2026-10-26 | 3533.1015625 | 3530.83 |

`river_discharge` maps to CEMS control (member 0); `river_discharge_memberNN` maps to CEMS `number` NN (01–50). Numeric mapping is fixed, not optimized. The JSON records all member-pair fingerprints and demeaned member correlations at every lead. `past_days=7` returns seven archived daily values, not seven complete issue trajectories; October 18 cannot be scored as a second full run.

## Single pre-registered L45 result

Tolerance unchanged: abs(OM − CEMS) ≤ max(0.5 m³/s, 0.005 × abs(CEMS)); adopt if at least 99% of all member×lead values pass on every matched issue. Relative differences use CEMS as denominator. No tuning or alternate threshold.

Issue 2026-10-19: **357/357** within tolerance; share **1.0**. Maximum absolute difference: **6.454999999999927 m³/s**. Maximum relative difference: **0.0011488394728673408** (fraction).

| lead | within/total | share | max abs m³/s | max relative (fraction) |
|---:|---:|---:|---:|---:|
| 1 | 51/51 | 1.0 | 4.573749999999563 | 0.0010861837369291044 |
| 2 | 51/51 | 1.0 | 5.5234375 | 0.0011077700654833719 |
| 3 | 51/51 | 1.0 | 5.580312500000218 | 0.0011197485410620305 |
| 4 | 51/51 | 1.0 | 5.3243750000001455 | 0.0010696567965284927 |
| 5 | 51/51 | 1.0 | 6.454999999999927 | 0.0011488394728673408 |
| 6 | 51/51 | 1.0 | 4.470937500000218 | 0.0011158618293216466 |
| 7 | 51/51 | 1.0 | 4.846562499999891 | 0.0011485362352559622 |

## Requests and limits

Zero network requests on this resume. Earlier worker CEMS request: rejected after 13.761870116002683 s. Parent's successful request: two issues, 714 records, 393.98245247999876 s, seven leads, 51 members. Operational archive untouched. Total CEMS attempts across these stages: 2. This is one-day source equivalence evidence, not a new stage-model evaluation.
