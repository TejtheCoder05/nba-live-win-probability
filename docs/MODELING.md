# Win-probability modeling design

## Dataset contract

One row is one emitted Phase 3 game state, and `home_win` is the eventual
game-level outcome. The split is fixed by season:

| Split | Season | Games | Rows |
|---|---:|---:|---:|
| Train | 2021-22 | 1,230 | 479,432 |
| Validation | 2022-23 | 1,230 | 481,931 |
| Test | 2023-24 | 1,230 | 476,516 |

Games and rows are never randomly split. A random row split would place states
from the same game, all carrying the same outcome, on both sides of the split.
That leaks game identity and later-game context into evaluation. The
forward-in-time season split is harder and more realistic: parameters learn
from one season, choices are made on the next, and the frozen result is assessed
on a future season.

Parquet files are read explicitly per game. This avoids PyArrow treating the
`season=...` directory as a second Hive-derived season column when `season` is
already materialized in each file.

## Feature contract

The ordered V1 feature vector is:

1. `score_differential`
2. `period`
3. `seconds_remaining_period`
4. `seconds_remaining_regulation`
5. `is_overtime`
6. `overtime_number`
7. `home_possession`
8. `possession_known`
9. `home_team_fouls_period`
10. `away_team_fouls_period`

These describe only the observable state at prediction time. The model excludes
`game_id`, event/action IDs, team IDs, descriptions, semantic event labels,
possession-reason text, and `home_win`. It also excludes absolute home/away
scores: adding them slightly worsened validation Brier (0.175847 versus
0.175840) and AUC, while duplicating information already represented by score
differential and game time. Team IDs are intentionally absent so V1 learns a
general game-state relationship rather than memorizing season-specific team
strength.

## Missing possession

Unknown possession is not guessed. `home_possession` is imputed to neutral
`0.5`, while `possession_known=0` tells the model that this value is missing.
All other selected features are complete.

Dropping unknown states removed 30,784 training rows (6.42%) and 30,959
validation rows (6.42%). Validation logistic Brier worsened from 0.175840 to
0.176139 and log loss from 0.521236 to 0.521963. V1 therefore keeps them.

## Repeated states and weighting

The training season has 335–508 states per game (mean 389.8, median 388.5,
95th percentile 428). That narrow distribution does not indicate severe game
weight imbalance. Comparisons on validation were:

| Training rows | Brier | Log loss | ECE |
|---|---:|---:|---:|
| Full, unweighted | 0.175840 | 0.521236 | 0.026252 |
| Full, equal total weight per game | 0.175767 | 0.521032 | 0.024389 |
| Last state per 30-second bucket | 0.175864 | 0.521222 | 0.025022 |

Game weighting's 0.000072 Brier gain was negligible given the already balanced
game sizes; time bucketing discarded 75% of training states and was worse.
The final pipeline retains all states without weights or random thinning.

## Preprocessing

`FeaturePreprocessor` fits means and population standard deviations only on
2021-22. It standardizes non-binary features, leaves binary features unscaled,
imputes only unknown possession to 0.5, checks finite output, and serializes to
JSON with its exact feature order and fitted season. Validation, test, and
inference only call `transform` on this frozen artifact.

## Models

The constant baseline emits the training row home-win rate, 0.541820. Logistic
regression uses the same transformed features and `C=1`. The PyTorch model is:

```text
10 inputs -> Linear(64) -> ReLU -> Dropout(0.2)
          -> Linear(32) -> ReLU -> Linear(1 logit)
```

It has 2,817 trainable parameters. Training uses `BCEWithLogitsLoss`, AdamW,
batch size 4,096, learning rate 0.001, weight decay 0.00001, seed 42, and CPU
(`torch.backends.mps.is_available()` was false). `BCEWithLogitsLoss` combines
sigmoid and binary cross-entropy in a numerically stable operation, so the
network does not contain a sigmoid training layer. Sigmoid is applied only when
probabilities are needed for evaluation or inference.

The checkpoint with the lowest validation Brier is retained. Early stopping
uses patience four. The selected run's best epoch was 7 and training stopped at
epoch 11.

## Calibration and selection discipline

Probability calibration asks whether, across many states assigned 70%, the home
team wins about 70% of the time. ECE is the row-count-weighted mean absolute gap
between predicted and observed rates in ten fixed-width probability bins.

Platt scaling was assessed without using test labels: the first 615 validation
games fit the calibrator and the later 615 assessed it. It worsened Brier from
0.177287 to 0.178921, log loss from 0.517799 to 0.522374, and ECE from 0.015424
to 0.038977. It is therefore not applied and no `calibration.json` is shipped.
Validation and test stay separate because using test outcomes for this choice
would turn the test set into another validation set and bias the final estimate.

The logistic baseline matters because it tests whether neural-network
complexity is useful. Here the MLP earns that complexity on the untouched test
season: Brier improves from 0.167731 to 0.163341 and log loss from 0.500815 to
0.482660. Accuracy changes little, demonstrating why accuracy alone is the
wrong objective: it ignores confidence. Brier measures squared probability
error; log loss strongly penalizes confident wrong predictions.

## Reproducible commands

```bash
# Train/validation dataset audit and logistic comparisons (does not read test)
python scripts/analyze_modeling_dataset.py

# Small validation-only MLP search and artifact staging (does not read test)
python scripts/tune_win_probability_model.py

# Run once after choices are frozen to evaluate 2023-24
python scripts/finalize_model_evaluation.py
```

Load the saved inference pipeline and pass one already reconstructed state:

```python
from src.models.inference import WinProbabilityPredictor
from src.paths import MODEL_ARTIFACT_DIR

predictor = WinProbabilityPredictor.load(MODEL_ARTIFACT_DIR)
home_win_probability = predictor.predict_one(game_state)
```

This class is the future connection point to live polling and the dashboard: the
live layer will reconstruct the same state, call this interface, and publish the
returned probability. No live endpoint, server, WebSocket, or frontend work is
part of Phases 4–5.

