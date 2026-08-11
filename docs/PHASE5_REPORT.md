# Phase 5 verification report

## Outcome

Phases 4 and 5 are complete. A leakage-safe season-split dataset, constant and
logistic baselines, a small PyTorch model, probability evaluation, persistent
artifacts, and a reusable inference interface are implemented and verified.
The 2023-24 test season was opened only after all feature, sampling,
hyperparameter, checkpoint, and calibration decisions were frozen.

## Source schema inspected

The existing Phase 3 files contain exactly 29 columns:

```text
season, game_id, source_event_index, action_number, action_id, clock,
action_type, sub_type, semantic_event, description, home_team_id,
away_team_id, home_score, away_score, score_differential, period,
seconds_remaining_period, seconds_remaining_regulation, is_overtime,
overtime_number, possession_team_id, home_possession, possession_known,
possession_reason, home_team_fouls_period, away_team_fouls_period,
foul_marker_team_count, foul_marker_matches, home_win
```

Representative games from all three seasons had identical schemas and dtypes.
Only `home_possession` is missing among the model candidates.

## Dataset audit

| Split | Games | Rows | Row home-win rate | Game home-win rate | Known possession | Mean states/game |
|---|---:|---:|---:|---:|---:|---:|
| Train 2021-22 | 1,230 | 479,432 | 54.182% | 54.390% | 93.579% | 389.78 |
| Validation 2022-23 | 1,230 | 481,931 | 58.174% | 58.049% | 93.576% | 391.81 |
| Test 2023-24 | 1,230 | 476,516 | 54.578% | 54.309% | 93.570% | 387.41 |

There are no cross-split game IDs. Selected numeric ranges were plausible: all
seasons cover periods 1–4 plus overtime, regulation time 0–2,880 seconds,
period time 0–720 seconds, and both positive and negative score differentials.
Missing selected values are 30,784 / 30,959 / 30,641 possession values in
train / validation / test and zero for every other V1 feature.

Unknown possession is kept with neutral 0.5 imputation and a knownness
indicator. Full, unweighted event states are used. See
[MODELING.md](MODELING.md) for the measured alternatives and rationale.

## Validation-only model selection

All candidates use hidden sizes 64/32, batch size 4,096, AdamW, weight decay
0.00001, seed 42, and early-stopping patience four.

| Learning rate | Dropout | Best / stopped epoch | Validation Brier | Log loss | ECE |
|---:|---:|---:|---:|---:|---:|
| 0.001 | 0.1 | 7 / 11 | 0.170374 | 0.500741 | 0.016302 |
| **0.001** | **0.2** | **7 / 11** | **0.170278** | **0.500537** | **0.014602** |
| 0.0003 | 0.1 | 20 / 24 | 0.170634 | 0.501516 | 0.023221 |

The selected 2,817-parameter model trained on CPU because MPS was built but not
available. Platt calibration was rejected after it worsened all primary
probability metrics on the held-out half of validation.

## Untouched 2023-24 results

| Model | Brier ↓ | Log loss ↓ | Accuracy | ROC-AUC | ECE ↓ |
|---|---:|---:|---:|---:|---:|
| Constant train home-win rate | 0.247920 | 0.688981 | 0.545782 | 0.500000 | 0.003962 |
| Logistic regression | 0.167731 | 0.500815 | 0.748758 | 0.828766 | 0.016424 |
| **Raw PyTorch MLP (selected)** | **0.163341** | **0.482660** | **0.749417** | **0.837340** | 0.016886 |

The constant model's low ECE is not skill: nearly every prediction is near
0.542, so it is globally calibrated while unable to discriminate states. Brier,
log loss, AUC, and reliability must be interpreted together.

## Reliability

| Predicted bin | Rows | Mean prediction | Observed home-win rate | Absolute gap |
|---|---:|---:|---:|---:|
| 0–10% | 47,115 | 3.10% | 3.93% | 0.82% |
| 10–20% | 20,714 | 14.95% | 13.04% | 1.91% |
| 20–30% | 25,297 | 25.34% | 21.03% | 4.31% |
| 30–40% | 37,063 | 35.26% | 29.84% | 5.42% |
| 40–50% | 52,656 | 45.31% | 41.74% | 3.57% |
| 50–60% | 73,572 | 55.10% | 55.27% | 0.17% |
| 60–70% | 62,746 | 64.75% | 64.33% | 0.42% |
| 70–80% | 46,553 | 74.71% | 72.30% | 2.41% |
| 80–90% | 35,448 | 84.99% | 83.12% | 1.86% |
| 90–100% | 75,352 | 96.75% | 96.91% | 0.15% |

Overall ECE is 0.016886. The largest gaps are in the 20–50% region; the model
is strongest at the extremes and around 50–70%. The reliability chart is saved
as `artifacts/win_probability_v1/reliability.png`.

## Time and score slices

| Time remaining | Rows | Brier | Log loss | ECE |
|---|---:|---:|---:|---:|
| >36m | 114,920 | 0.235139 | 0.662711 | 0.024946 |
| 24–36m | 118,852 | 0.193943 | 0.568941 | 0.032662 |
| 12–24m | 119,069 | 0.145938 | 0.446897 | 0.025733 |
| 6–12m | 58,159 | 0.105272 | 0.331041 | 0.016809 |
| 2–6m | 37,525 | 0.074005 | 0.233552 | 0.015048 |
| 0–2m | 27,991 | 0.053075 | 0.178209 | 0.035389 |

| Absolute score differential | Rows | Brier | Log loss | ECE |
|---|---:|---:|---:|---:|
| 0–2 | 100,883 | 0.242300 | 0.677372 | 0.004528 |
| 3–5 | 98,961 | 0.220908 | 0.630797 | 0.018418 |
| 6–10 | 117,686 | 0.175036 | 0.523713 | 0.034604 |
| 11–20 | 115,818 | 0.090566 | 0.307673 | 0.017880 |
| 20+ | 43,168 | 0.010214 | 0.045591 | 0.007581 |

Probability quality improves as time expires and leads grow, which is the
expected information pattern. Overtime has only 2,918 rows and higher ECE
(0.103358), so that slice is less stable and is a clear future-improvement area.

## Trajectory checks

Four real test games were saved in `trajectory_samples.json` and plotted in
`game_trajectories.png`:

- close: `0022300068`, 103–102;
- comeback: `0022301034`, home win 120–118 after reaching 3.6%;
- blowout: `0022300529`, 139–77, rising to effectively 100%;
- overtime: `0022300007`, 115–113 after being 6.9% late in Q4.

The paths move in both directions as scores and possession change; no monotonic
constraint is imposed. A known limitation is that the raw statistical model is
not given an explicit game-status/terminal flag. At 0:00 in one- and two-point
wins it emitted roughly 62–77% rather than logical certainty. Future live
integration should apply a status-aware final-state rule after the NBA marks a
game final; no such live logic was added in this phase.

## Local inference performance

After 100 warm-up calls on this Mac/CPU:

| Mode | Median | p95 | Throughput |
|---|---:|---:|---:|
| One reconstructed state | 0.316 ms | 0.325 ms | 3,167 states/s |
| Batch of 32 | 0.361 ms/batch | 0.370 ms/batch | 88,188 states/s |

These are measured end-to-end local predictor calls, including feature
extraction/preprocessing, not an invented target.

## Artifacts

`artifacts/win_probability_v1/` contains:

- `best_model.pt` — PyTorch `state_dict`;
- `model_config.json`, `model_metadata.json`;
- `feature_metadata.json`, `preprocessing.json`;
- `logistic_baseline.json`;
- `training_history.json`, `tuning_report.json`;
- `phase4_validation_analysis.json`, `dataset_summary.json`;
- `evaluation_metrics.json`, `calibration_bins.json`,
  `time_slice_metrics.json`;
- `trajectory_samples.json`, `reliability.png`,
  `game_trajectories.png`;
- `latency.json`.

There is no calibration artifact because Platt scaling was not justified.
Artifacts are reproducible build outputs and remain gitignored.

## Verification and limitations

- 115 tests pass, including all original 98 tests.
- All model and script modules compile under Python 3.12.10.
- Saved/load predictions are identical in tests.
- Dependencies are pinned to the verified environment.
- The model has no pre-game team-strength context, player/injury information,
  timeout state, or status-aware terminal override.
- Event states are correlated within games. Season-level isolation prevents
  leakage, but row-level metrics still describe the emitted-state distribution.
- This phase intentionally does not implement live NBA endpoints, Flask,
  WebSockets, or frontend code.
