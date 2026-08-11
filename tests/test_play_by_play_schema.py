"""Validate that saved play-by-play carries what future feature work needs.

This is a *schema and availability* test, not a data-quality audit. It asks one
question: does a saved PlayByPlayV3 response actually contain usable
**period**, **clock**, **scoring**, and **event/action** information?

If these assertions hold, the Phase 3 feature pipeline (score differential,
seconds remaining, team fouls, possession) has the raw material it needs. If any
fails, either the API changed shape or our saved data is corrupt, and we want to
know that before building features on top of it.

Deliberately NOT asserted: that scores increase monotonically through the game.
Real NBA play-by-play contains scoring corrections and revisions, so such an
assertion would test the league's data entry rather than our schema, and would
fail intermittently on perfectly usable games.
"""

from __future__ import annotations

import re

import pandas as pd
import pytest

# --- The four conceptual categories, mapped to real PlayByPlayV3 fields ------
PERIOD_FIELDS = ["period"]
CLOCK_FIELDS = ["clock"]
SCORING_FIELDS = ["scoreHome", "scoreAway"]
EVENT_FIELDS = ["actionType", "subType", "description", "actionNumber"]
TEAM_FIELDS = ["teamId", "teamTricode"]

# PlayByPlayV3 reports the clock as an ISO-8601 duration, e.g. "PT12M00.00S"
# or "PT05M39.00S". Seconds may carry a fractional part.
CLOCK_PATTERN = re.compile(r"^PT(\d+)M(\d+(?:\.\d+)?)S$")

# Longest possible period length in seconds (regulation quarter = 12 minutes).
MAX_PERIOD_SECONDS = 12 * 60


def parse_clock_seconds(clock: str) -> float:
    """Convert an ISO-8601 duration clock string to seconds remaining."""
    match = CLOCK_PATTERN.match(str(clock).strip())
    if match is None:
        raise ValueError(f"Unparseable clock value: {clock!r}")
    minutes, seconds = match.groups()
    return int(minutes) * 60 + float(seconds)


def non_empty(series: pd.Series) -> pd.Series:
    """Subset of values that are neither null nor an empty/whitespace string.

    This DROPS rows. Use :func:`blank_to_na` when row alignment must be kept.
    """
    as_text = series.astype(str).str.strip()
    return series[series.notna() & (as_text != "") & (as_text.str.lower() != "none")]


def blank_to_na(series: pd.Series) -> pd.Series:
    """Replace empty/whitespace strings with NA, preserving length and index.

    The scoring columns are blank on non-scoring plays, and forward-filling them
    only works if those blanks become NA *in place* rather than being dropped.
    """
    as_text = series.astype(str).str.strip()
    return series.where(series.notna() & (as_text != "") & (as_text.str.lower() != "none"))


# --- Structural sanity -------------------------------------------------------


def test_events_are_non_empty(events: pd.DataFrame) -> None:
    """A saved game must contain events at all."""
    assert len(events) > 0, "Play-by-play contains no events"


def test_all_expected_columns_present(events: pd.DataFrame) -> None:
    """Every field the feature pipeline will read must exist."""
    required = PERIOD_FIELDS + CLOCK_FIELDS + SCORING_FIELDS + EVENT_FIELDS + TEAM_FIELDS
    missing = [column for column in required if column not in events.columns]
    assert not missing, f"Missing expected columns: {missing}"


def test_game_id_is_attached_and_consistent(events: pd.DataFrame) -> None:
    """All events belong to exactly one game, identified by a 10-char GAME_ID."""
    assert "gameId" in events.columns
    game_ids = events["gameId"].unique()
    assert len(game_ids) == 1, f"Events span multiple games: {game_ids}"
    assert re.fullmatch(r"\d{10}", str(game_ids[0])), f"Unexpected GAME_ID: {game_ids[0]!r}"


# --- Category 1: period ------------------------------------------------------


def test_period_is_usable(events: pd.DataFrame) -> None:
    """Period must be present, integer-valued, and start at 1."""
    periods = pd.to_numeric(events["period"], errors="coerce")

    assert periods.notna().all(), "Some events have a non-numeric period"
    assert (periods >= 1).all(), "Found a period below 1"
    assert periods.min() == 1, "Play-by-play should include the first period"
    # Regulation is 4; anything beyond is overtime. 10 is a generous ceiling.
    assert periods.max() <= 10, f"Implausible max period: {periods.max()}"


# --- Category 2: clock -------------------------------------------------------


def test_clock_is_parseable_to_seconds(events: pd.DataFrame) -> None:
    """Every clock value must parse into a sane seconds-remaining number.

    This is what makes the eventual `seconds_remaining` feature possible: the
    API gives a duration string, not a number, so the format must be reliable.
    """
    clocks = non_empty(events["clock"])
    assert len(clocks) > 0, "No clock values present"

    unparseable = [value for value in clocks.unique() if CLOCK_PATTERN.match(str(value).strip()) is None]
    assert not unparseable, f"Clock values not in PT#M#S form: {unparseable[:5]}"

    seconds = clocks.map(parse_clock_seconds)
    assert (seconds >= 0).all(), "Negative time remaining"
    assert (seconds <= MAX_PERIOD_SECONDS).all(), (
        f"Clock exceeds a 12-minute period: max={seconds.max()}"
    )


def test_clock_and_period_together_order_the_game(events: pd.DataFrame) -> None:
    """Period + clock must be enough to place an event in game time.

    Within a period the clock counts DOWN, so ordering events by
    (period ascending, seconds-remaining descending) should reproduce the
    original event order closely. This confirms the two fields are jointly
    sufficient to build a game-state timeline.
    """
    frame = pd.DataFrame(
        {
            "period": pd.to_numeric(events["period"], errors="coerce"),
            "seconds": events["clock"].map(parse_clock_seconds),
            "action_number": pd.to_numeric(events["actionNumber"], errors="coerce"),
        }
    ).dropna()

    reordered = frame.sort_values(["period", "seconds"], ascending=[True, False])

    # Rank correlation, not raw-value correlation: we care whether the ORDER
    # agrees, not whether actionNumber grows linearly. The committed fixture is
    # a sampled slice with large gaps in actionNumber, which would depress a
    # value-based score even when the ordering is perfect.
    #
    # Correlating ranks IS Spearman's coefficient, computed here with plain
    # pandas so the project does not gain a scipy dependency for one assertion.
    ranks = reordered["action_number"].rank().reset_index(drop=True)
    correlation = ranks.corr(pd.Series(range(len(reordered)), dtype="float64"))
    assert correlation > 0.99, (
        f"Period+clock ordering does not track event order (rank corr={correlation:.4f})"
    )


# --- Category 3: scoring -----------------------------------------------------


def test_scoring_fields_are_present_and_numeric(events: pd.DataFrame) -> None:
    """scoreHome/scoreAway must exist and parse as non-negative numbers.

    Both columns are strings and are only populated on scoring plays, so blanks
    are expected and legitimate; the feature pipeline forward-fills them. We
    only require that the values which ARE populated are usable numbers.
    """
    for column in SCORING_FIELDS:
        populated = non_empty(events[column])
        assert len(populated) > 0, f"{column} is empty for every event"

        numeric = pd.to_numeric(populated, errors="coerce")
        bad = populated[numeric.isna()]
        assert bad.empty, f"Non-numeric {column} values: {bad.unique()[:5].tolist()}"
        assert (numeric >= 0).all(), f"Negative {column} value found"


def test_score_differential_is_derivable(events: pd.DataFrame) -> None:
    """Forward-filling both score columns yields a score at every event.

    This is precisely how the `score_differential` feature will be built, so
    proving it works here de-risks Phase 3.
    """
    # blank_to_na (not non_empty) so blanks become NA in place and ffill can
    # carry the last known score forward across non-scoring events.
    scores = events[SCORING_FIELDS].apply(
        lambda column: pd.to_numeric(blank_to_na(column), errors="coerce")
    )
    # Games start 0-0, so any leading NA before the first scoring play is 0.
    filled = scores.ffill().fillna(0)

    assert len(filled) == len(events), "Forward-fill must preserve every event row"
    assert filled["scoreHome"].notna().all()
    assert filled["scoreAway"].notna().all()

    differential = filled["scoreHome"] - filled["scoreAway"]
    assert len(differential) == len(events), "Differential must exist for every event"
    assert differential.abs().max() < 100, "Implausible score differential"


# --- Category 4: event / action information ---------------------------------


def test_action_information_is_usable(events: pd.DataFrame) -> None:
    """actionType must identify what happened for the great majority of events.

    A minority of rows are secondary credit events (BLOCK, STEAL) that carry a
    blank actionType and describe the play only in `description`, so we require
    a strong majority rather than universal coverage.
    """
    labelled = non_empty(events["actionType"])
    coverage = len(labelled) / len(events)
    assert coverage > 0.8, f"Only {coverage:.1%} of events have an actionType"

    # The event categories the possession engine will eventually branch on.
    action_types = {value.strip().lower() for value in labelled.unique()}
    for expected in ["made shot", "missed shot", "rebound", "turnover"]:
        assert expected in action_types, f"No '{expected}' events found; got {sorted(action_types)}"


def test_every_event_has_a_description(events: pd.DataFrame) -> None:
    """`description` is the human-readable fallback when actionType is blank."""
    described = non_empty(events["description"])
    assert len(described) / len(events) > 0.9, "Most events should carry a description"


def test_events_can_be_attributed_to_a_team(events: pd.DataFrame) -> None:
    """Team attribution is required for per-team fouls, timeouts, possession."""
    attributed = non_empty(events["teamTricode"])
    coverage = len(attributed) / len(events)
    assert coverage > 0.8, f"Only {coverage:.1%} of events name a team"

    tricodes = {value.strip() for value in attributed.unique()}
    assert len(tricodes) == 2, f"Expected exactly 2 teams, found {sorted(tricodes)}"


def test_foul_and_timeout_events_are_identifiable(events: pd.DataFrame) -> None:
    """Fouls and timeouts appear as events, which is how we will count them.

    Neither a running team-foul count nor a timeouts-remaining value is provided
    by the API, so both features depend on these events being findable.
    """
    action_type = events["actionType"].astype(str)
    assert action_type.str.contains("foul", case=False, na=False).any(), "No foul events found"
    assert action_type.str.contains("timeout", case=False, na=False).any(), "No timeout events found"


@pytest.mark.parametrize("column", PERIOD_FIELDS + CLOCK_FIELDS + EVENT_FIELDS[:1])
def test_core_columns_have_no_nulls(events: pd.DataFrame, column: str) -> None:
    """Period, clock, and actionType must be present on every row (possibly blank,
    but never null) so downstream code never has to guard against NaN."""
    assert events[column].notna().all(), f"{column} contains null values"
