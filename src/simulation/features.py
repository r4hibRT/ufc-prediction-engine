"""Point-in-time feature construction.

Implements docs/feature-spec.md. Every feature here is computed from a
fighter's state as it stood strictly BEFORE the bout being described --
`FighterState.update()` is called only after a row has been emitted.

Mirroring is declarative. Each feature registers one of three behaviours and
the mirrored row is generated from that registry rather than hand-written, so
`assert_mirror_rules()` can verify the whole set instead of trusting that two
long dict literals were kept in sync.
"""

import math
from collections import deque

# --- mirroring rules -------------------------------------------------------

NEG = "neg"    # value negates on the mirrored row
SAME = "same"  # value is unchanged
SWAP = "swap"  # the a/b pair exchanges values

PEAK_AGE = 29.0
ELITE_RATING = 1600.0
RECENT_FORM_WINDOW = 5
MOMENTUM_WINDOW = 3

NEG_FEATURES = [
    # Block A
    "rating_diff", "peak_rating_diff", "rating_momentum_diff",
    "time_since_peak_diff", "volatility_diff",
    # Block B
    "win_streak_diff", "losing_streak_diff",
    "recent_form_3_diff", "recent_form_5_diff",
    # Block C
    "ko_loss_rate_diff", "sub_loss_rate_diff", "finish_rate_diff",
    "career_rounds_diff", "strikes_absorbed_total_diff",
    "knockdowns_absorbed_rate_diff",
    # Block D
    "layoff_diff", "fights_last_1yr_diff", "fights_last_2yr_diff",
    # Block E
    "avg_opponent_rating_diff", "max_opponent_rating_diff", "elite_share_diff",
    # Block F
    "reach_diff", "height_diff", "age_diff", "southpaw_vs_orthodox",
    # Block G
    "sig_strikes_pm_diff", "strikes_absorbed_pm_diff",
    "striking_differential_diff", "td_accuracy_diff", "td_defence_diff",
    "td_rate_diff", "control_time_pm_diff", "sub_attempt_rate_diff",
    "knockdown_rate_diff",
    # Block H
    "grappler_vs_defence", "volume_vs_absorption", "finisher_vs_durability",
    # Block I
    "five_round_experience_interaction", "division_debut_days_diff",
]

SAME_FEATURES = [
    "rd_sum", "abs_reach_diff", "is_title_fight", "is_defence", "same_stance",
]

SWAP_PAIRS = [
    ("last_was_ko_loss_a", "last_was_ko_loss_b"),
    ("layoff_a", "layoff_b"),
    ("age_a", "age_b"),
    ("age_from_peak_a", "age_from_peak_b"),
    ("division_experience_a", "division_experience_b"),
]

FEATURE_COLUMNS = (
    NEG_FEATURES
    + SAME_FEATURES
    + [name for pair in SWAP_PAIRS for name in pair]
)

BLOCKS = {
    "A_skill": ["rating_diff", "rd_sum", "peak_rating_diff",
                "rating_momentum_diff", "time_since_peak_diff", "volatility_diff"],
    "B_form": ["win_streak_diff", "losing_streak_diff", "recent_form_3_diff",
               "recent_form_5_diff", "last_was_ko_loss_a", "last_was_ko_loss_b"],
    "C_durability": ["ko_loss_rate_diff", "sub_loss_rate_diff", "finish_rate_diff",
                     "career_rounds_diff", "strikes_absorbed_total_diff",
                     "knockdowns_absorbed_rate_diff"],
    "D_activity": ["layoff_diff", "layoff_a", "layoff_b",
                   "fights_last_1yr_diff", "fights_last_2yr_diff"],
    "E_schedule": ["avg_opponent_rating_diff", "max_opponent_rating_diff",
                   "elite_share_diff"],
    "F_physical": ["reach_diff", "height_diff", "age_diff", "age_a", "age_b",
                   "abs_reach_diff", "age_from_peak_a", "age_from_peak_b",
                   "southpaw_vs_orthodox", "same_stance"],
    "G_rates": ["sig_strikes_pm_diff", "strikes_absorbed_pm_diff",
                "striking_differential_diff", "td_accuracy_diff",
                "td_defence_diff", "td_rate_diff", "control_time_pm_diff",
                "sub_attempt_rate_diff", "knockdown_rate_diff"],
    "H_mismatch": ["grappler_vs_defence", "volume_vs_absorption",
                   "finisher_vs_durability"],
    "I_context": ["is_title_fight", "is_defence", "division_experience_a",
                  "division_experience_b", "five_round_experience_interaction",
                  "division_debut_days_diff"],
}

NAN = float("nan")


def _ratio(numerator, denominator):
    """Undefined rather than zero when nothing was attempted.

    The deleted clustering code used `.replace(0, 1)` here, which handed a
    fighter who had never faced a takedown attempt a 100% takedown defence.
    """
    if denominator is None or denominator == 0:
        return NAN
    return numerator / denominator


def fight_seconds(round_, time_str):
    """Elapsed fight time: five minutes per completed round plus the last one."""
    try:
        completed = (int(round_) - 1) * 300
        mins, secs = time_str.split(":")
        return completed + int(mins) * 60 + int(secs)
    except (TypeError, ValueError, AttributeError):
        return None


class FighterState:
    """Everything known about a fighter from their previous bouts only."""

    def __init__(self):
        self.bouts = 0          # results that stood
        self.appearances = 0    # times in the cage, no contests included
        self.wins = 0
        self.win_streak = 0
        self.loss_streak = 0
        self.recent = deque(maxlen=RECENT_FORM_WINDOW)
        self.decision_count = 0

        self.ko_losses = 0
        self.sub_losses = 0
        self.finishes = 0
        self.last_was_ko_loss = 0

        self.career_seconds = 0
        self.bout_dates = []
        self.division_bouts = {}
        self.division_first_date = {}
        self.last_bout_date = None

        self.rating = None
        self.rd = None
        self.volatility = None
        self.rating_history = deque(maxlen=MOMENTUM_WINDOW + 1)
        self.peak_rating = None
        self.peak_date = None

        self.opponent_ratings = []

        # bout_stats accumulators
        self.sig_landed = 0
        self.sig_attempted = 0
        self.sig_absorbed = 0
        self.td_landed = 0
        self.td_attempted = 0
        self.opp_td_landed = 0
        self.opp_td_attempted = 0
        self.control_seconds = 0
        self.sub_attempts = 0
        self.knockdowns = 0
        self.knockdowns_absorbed = 0

    # --- derived quantities, all point-in-time -----------------------------

    @property
    def has_rating(self):
        return self.rating is not None

    @property
    def minutes(self):
        return self.career_seconds / 60.0 if self.career_seconds else 0.0

    def per_minute(self, total):
        return _ratio(total, self.minutes)

    def per_15(self, total):
        rate = _ratio(total, self.minutes)
        return NAN if rate != rate else rate * 15.0

    @property
    def recent_form_3(self):
        if len(self.recent) < MOMENTUM_WINDOW:
            return NAN
        window = list(self.recent)[-MOMENTUM_WINDOW:]
        return sum(window) / len(window)

    @property
    def recent_form_5(self):
        if not self.recent:
            return NAN
        return sum(self.recent) / len(self.recent)

    @property
    def rating_momentum(self):
        """Rating change over the last MOMENTUM_WINDOW bouts."""
        if len(self.rating_history) < 2:
            return NAN
        return self.rating_history[-1] - self.rating_history[0]

    def time_since_peak(self, bout_date):
        if self.peak_date is None:
            return NAN
        return (bout_date - self.peak_date).days

    def layoff(self, bout_date):
        if self.last_bout_date is None:
            return NAN
        return (bout_date - self.last_bout_date).days

    def fights_within(self, bout_date, days):
        cutoff = bout_date - _timedelta(days)
        return sum(1 for d in self.bout_dates if d >= cutoff)

    @property
    def ko_loss_rate(self):
        return _ratio(self.ko_losses, self.bouts)

    @property
    def sub_loss_rate(self):
        return _ratio(self.sub_losses, self.bouts)

    @property
    def finish_rate(self):
        return _ratio(self.finishes, self.bouts)

    @property
    def avg_opponent_rating(self):
        if not self.opponent_ratings:
            return NAN
        return sum(self.opponent_ratings) / len(self.opponent_ratings)

    @property
    def max_opponent_rating(self):
        return max(self.opponent_ratings) if self.opponent_ratings else NAN

    @property
    def elite_share(self):
        if not self.opponent_ratings:
            return NAN
        elite = sum(1 for r in self.opponent_ratings if r >= ELITE_RATING)
        return elite / len(self.opponent_ratings)

    @property
    def td_defence(self):
        rate = _ratio(self.opp_td_landed, self.opp_td_attempted)
        return NAN if rate != rate else 1.0 - rate

    @property
    def td_rate(self):
        return self.per_15(self.td_landed)

    @property
    def knockdown_rate(self):
        return self.per_15(self.knockdowns)

    def division_debut_days(self, weight_class, bout_date):
        first = self.division_first_date.get(weight_class)
        if first is None:
            return NAN
        return (bout_date - first).days

    def snapshot(self, bout_date):
        """This fighter's state entering a bout, for the bout_snapshots table.

        NaN becomes None so psycopg2 writes SQL NULL rather than the string
        'nan'. Undefined is genuinely different from zero here: a debutant has
        no strength of schedule, they do not have a strength of schedule of 0.
        """
        def clean(value):
            if value is None:
                return None
            return None if value != value else value

        return {
            "bouts_before": self.bouts,
            "appearances_before": self.appearances,
            "wins_before": self.wins,
            "win_streak": self.win_streak,
            "loss_streak": self.loss_streak,
            "recent_form_5": clean(self.recent_form_5),
            "rating_before": clean(self.rating),
            "rd_before": clean(self.rd),
            "peak_rating_before": clean(self.peak_rating),
            "layoff_days": clean(self.layoff(bout_date)),
            "ko_losses": self.ko_losses,
            "sub_losses": self.sub_losses,
            "finishes": self.finishes,
            "career_seconds": self.career_seconds,
            "sig_landed": self.sig_landed,
            "sig_attempted": self.sig_attempted,
            "sig_absorbed": self.sig_absorbed,
            "td_landed": self.td_landed,
            "td_attempted": self.td_attempted,
            "opp_td_landed": self.opp_td_landed,
            "opp_td_attempted": self.opp_td_attempted,
            "control_seconds": self.control_seconds,
            "sub_attempts": self.sub_attempts,
            "knockdowns": self.knockdowns,
            "knockdowns_absorbed": self.knockdowns_absorbed,
            "avg_opponent_rating": clean(self.avg_opponent_rating),
            "max_opponent_rating": clean(self.max_opponent_rating),
        }

    # --- state transition, called only AFTER a row has been emitted --------

    def update(self, *, date, weight_class, result, method, seconds,
               rating, rd, volatility, opponent_prior_rating, stats, opp_stats,
               counts_result=True):
        """Advance state after a bout.

        `counts_result=False` for a no contest: the fighter still stepped into
        the cage, absorbed damage and used up calendar, so activity, cage time,
        strength of schedule and every fight statistic accumulate -- but the
        result is void, so the win-loss record, streaks, form and finish rates
        do not move. `bouts` counts results; `appearances` counts occasions.
        """
        # --- things that happened regardless of whether the result stood ---
        self.appearances += 1
        self.bout_dates.append(date)
        self.last_bout_date = date

        self.division_bouts[weight_class] = self.division_bouts.get(weight_class, 0) + 1
        self.division_first_date.setdefault(weight_class, date)

        if seconds:
            self.career_seconds += seconds

        if opponent_prior_rating is not None:
            self.opponent_ratings.append(opponent_prior_rating)

        if stats:
            self.sig_landed += stats["sig_strikes_landed"] or 0
            self.sig_attempted += stats["sig_strikes_attempted"] or 0
            self.td_landed += stats["takedowns_landed"] or 0
            self.td_attempted += stats["takedowns_attempted"] or 0
            self.control_seconds += stats["control_time_seconds"] or 0
            self.sub_attempts += stats["submission_attempts"] or 0
            self.knockdowns += stats["knockdowns"] or 0

        if opp_stats:
            self.sig_absorbed += opp_stats["sig_strikes_landed"] or 0
            self.opp_td_landed += opp_stats["takedowns_landed"] or 0
            self.opp_td_attempted += opp_stats["takedowns_attempted"] or 0
            self.knockdowns_absorbed += opp_stats["knockdowns"] or 0

        if not counts_result:
            return

        # --- things that depend on the result standing ---
        self.bouts += 1

        if result == 1:
            self.wins += 1
            self.win_streak += 1
            self.loss_streak = 0
        elif result == 0:
            self.win_streak = 0
            self.loss_streak += 1
        else:
            self.win_streak = 0
            self.loss_streak = 0
        self.recent.append(result)

        if method == "Decision":
            self.decision_count += 1

        is_finish_method = method in ("KO/TKO", "Submission")
        if result == 1 and is_finish_method:
            self.finishes += 1

        self.last_was_ko_loss = 1 if (result == 0 and method == "KO/TKO") else 0
        if result == 0:
            if method == "KO/TKO":
                self.ko_losses += 1
            elif method == "Submission":
                self.sub_losses += 1

        if rating is not None:
            self.rating = rating
            self.rd = rd
            self.volatility = volatility
            self.rating_history.append(rating)
            if self.peak_rating is None or rating > self.peak_rating:
                self.peak_rating = rating
                self.peak_date = date



def _timedelta(days):
    from datetime import timedelta
    return timedelta(days=days)


def _sub(x, y):
    """Difference that stays NaN if either side is unknown."""
    if x != x or y != y or x is None or y is None:
        return NAN
    return x - y


def _mul(x, y):
    if x != x or y != y or x is None or y is None:
        return NAN
    return x * y


def build_feature_row(st_a, st_b, phys_a, phys_b, ctx):
    """One row of features from fighter A's perspective.

    st_a / st_b are pre-bout FighterState objects. phys_* carry the static
    physical attributes. ctx carries bout context (date, weight class, title
    flags).
    """
    date = ctx["date"]
    weight_class = ctx["weight_class"]
    is_title = int(ctx["is_title_fight"])

    row = {}

    # --- Block A: skill ---
    row["rating_diff"] = _sub(st_a.rating, st_b.rating)
    row["rd_sum"] = (st_a.rd or 0) + (st_b.rd or 0)
    row["peak_rating_diff"] = _sub(st_a.peak_rating, st_b.peak_rating)
    row["rating_momentum_diff"] = _sub(st_a.rating_momentum, st_b.rating_momentum)
    row["time_since_peak_diff"] = _sub(st_a.time_since_peak(date), st_b.time_since_peak(date))
    row["volatility_diff"] = _sub(st_a.volatility, st_b.volatility)

    # --- Block B: form ---
    row["win_streak_diff"] = st_a.win_streak - st_b.win_streak
    row["losing_streak_diff"] = st_a.loss_streak - st_b.loss_streak
    row["recent_form_3_diff"] = _sub(st_a.recent_form_3, st_b.recent_form_3)
    row["recent_form_5_diff"] = _sub(st_a.recent_form_5, st_b.recent_form_5)
    row["last_was_ko_loss_a"] = st_a.last_was_ko_loss
    row["last_was_ko_loss_b"] = st_b.last_was_ko_loss

    # --- Block C: durability ---
    row["ko_loss_rate_diff"] = _sub(st_a.ko_loss_rate, st_b.ko_loss_rate)
    row["sub_loss_rate_diff"] = _sub(st_a.sub_loss_rate, st_b.sub_loss_rate)
    row["finish_rate_diff"] = _sub(st_a.finish_rate, st_b.finish_rate)
    row["career_rounds_diff"] = (st_a.career_seconds - st_b.career_seconds) / 300.0
    row["strikes_absorbed_total_diff"] = st_a.sig_absorbed - st_b.sig_absorbed
    row["knockdowns_absorbed_rate_diff"] = _sub(
        st_a.per_15(st_a.knockdowns_absorbed), st_b.per_15(st_b.knockdowns_absorbed))

    # --- Block D: activity ---
    layoff_a = st_a.layoff(date)
    layoff_b = st_b.layoff(date)
    row["layoff_diff"] = _sub(layoff_a, layoff_b)
    row["layoff_a"] = layoff_a
    row["layoff_b"] = layoff_b
    row["fights_last_1yr_diff"] = st_a.fights_within(date, 365) - st_b.fights_within(date, 365)
    row["fights_last_2yr_diff"] = st_a.fights_within(date, 730) - st_b.fights_within(date, 730)

    # --- Block E: strength of schedule ---
    row["avg_opponent_rating_diff"] = _sub(st_a.avg_opponent_rating, st_b.avg_opponent_rating)
    row["max_opponent_rating_diff"] = _sub(st_a.max_opponent_rating, st_b.max_opponent_rating)
    row["elite_share_diff"] = _sub(st_a.elite_share, st_b.elite_share)

    # --- Block F: physical ---
    reach_diff = _sub(phys_a["reach"], phys_b["reach"])
    age_a = phys_a["age_at"](date)
    age_b = phys_b["age_at"](date)
    row["reach_diff"] = reach_diff
    row["height_diff"] = _sub(phys_a["height"], phys_b["height"])
    row["age_diff"] = _sub(age_a, age_b)
    row["age_a"] = age_a
    row["age_b"] = age_b
    row["abs_reach_diff"] = NAN if reach_diff != reach_diff else abs(reach_diff)
    row["age_from_peak_a"] = NAN if age_a != age_a else abs(age_a - PEAK_AGE)
    row["age_from_peak_b"] = NAN if age_b != age_b else abs(age_b - PEAK_AGE)

    stance_a, stance_b = phys_a["stance"], phys_b["stance"]
    if not stance_a or not stance_b:
        row["same_stance"] = NAN
        row["southpaw_vs_orthodox"] = NAN
    else:
        row["same_stance"] = int(stance_a == stance_b)
        # +1 when A is the southpaw facing an orthodox opponent, -1 reversed.
        a_south = stance_a == "Southpaw"
        b_south = stance_b == "Southpaw"
        orth = {"Orthodox"}
        if a_south and stance_b in orth:
            row["southpaw_vs_orthodox"] = 1
        elif b_south and stance_a in orth:
            row["southpaw_vs_orthodox"] = -1
        else:
            row["southpaw_vs_orthodox"] = 0

    # --- Block G: point-in-time performance rates ---
    sig_pm_a, sig_pm_b = st_a.per_minute(st_a.sig_landed), st_b.per_minute(st_b.sig_landed)
    abs_pm_a, abs_pm_b = st_a.per_minute(st_a.sig_absorbed), st_b.per_minute(st_b.sig_absorbed)
    row["sig_strikes_pm_diff"] = _sub(sig_pm_a, sig_pm_b)
    row["strikes_absorbed_pm_diff"] = _sub(abs_pm_a, abs_pm_b)
    row["striking_differential_diff"] = _sub(_sub(sig_pm_a, abs_pm_a), _sub(sig_pm_b, abs_pm_b))
    row["td_accuracy_diff"] = _sub(_ratio(st_a.td_landed, st_a.td_attempted),
                                   _ratio(st_b.td_landed, st_b.td_attempted))
    row["td_defence_diff"] = _sub(st_a.td_defence, st_b.td_defence)
    row["td_rate_diff"] = _sub(st_a.td_rate, st_b.td_rate)
    row["control_time_pm_diff"] = _sub(st_a.per_minute(st_a.control_seconds),
                                       st_b.per_minute(st_b.control_seconds))
    row["sub_attempt_rate_diff"] = _sub(st_a.per_15(st_a.sub_attempts),
                                        st_b.per_15(st_b.sub_attempts))
    row["knockdown_rate_diff"] = _sub(st_a.knockdown_rate, st_b.knockdown_rate)

    # --- Block H: stylistic mismatch ---
    # Each term is A's strength against B's corresponding weakness, minus the
    # reverse, so the whole feature negates cleanly on the mirror.
    row["grappler_vs_defence"] = _sub(
        _mul(st_a.td_rate, _sub(1.0, st_b.td_defence)),
        _mul(st_b.td_rate, _sub(1.0, st_a.td_defence)))
    row["volume_vs_absorption"] = _sub(_mul(sig_pm_a, abs_pm_b), _mul(sig_pm_b, abs_pm_a))
    row["finisher_vs_durability"] = _sub(
        _mul(st_a.knockdown_rate, st_b.ko_loss_rate),
        _mul(st_b.knockdown_rate, st_a.ko_loss_rate))

    # --- Block I: context ---
    row["is_title_fight"] = is_title
    row["is_defence"] = int(ctx["is_defence"])
    row["division_experience_a"] = int(st_a.division_bouts.get(weight_class, 0) > 0)
    row["division_experience_b"] = int(st_b.division_bouts.get(weight_class, 0) > 0)

    pct_dist_a = _ratio(st_a.decision_count, st_a.bouts)
    pct_dist_b = _ratio(st_b.decision_count, st_b.bouts)
    row["five_round_experience_interaction"] = _mul(is_title, _sub(pct_dist_a, pct_dist_b))

    row["division_debut_days_diff"] = _sub(st_a.division_debut_days(weight_class, date),
                                           st_b.division_debut_days(weight_class, date))

    return row


def mirror_row(row):
    """Derive the opposite-perspective row from the mirroring registry."""
    mirrored = {}
    for name in NEG_FEATURES:
        value = row[name]
        mirrored[name] = value if value != value else -value
    for name in SAME_FEATURES:
        mirrored[name] = row[name]
    for a_name, b_name in SWAP_PAIRS:
        mirrored[a_name] = row[b_name]
        mirrored[b_name] = row[a_name]
    return mirrored


def assert_mirror_rules(row):
    """Verify a row's mirror behaves exactly as declared.

    Mirroring twice must return the original, every NEG feature must negate,
    every SAME feature must hold, and every SWAP pair must exchange. Getting
    any of this wrong is otherwise silent.
    """
    once = mirror_row(row)
    twice = mirror_row(once)

    for name in FEATURE_COLUMNS:
        original, back = row[name], twice[name]
        if original != original and back != back:
            continue
        assert math.isclose(original, back, rel_tol=1e-9, abs_tol=1e-9), (
            f"{name}: double mirror changed {original} -> {back}")

    for name in NEG_FEATURES:
        value, flipped = row[name], once[name]
        if value != value:
            continue
        assert math.isclose(value, -flipped, rel_tol=1e-9, abs_tol=1e-9), (
            f"{name} declared NEG but {value} -> {flipped}")

    for name in SAME_FEATURES:
        value, held = row[name], once[name]
        if value != value:
            continue
        assert value == held, f"{name} declared SAME but {value} -> {held}"

    for a_name, b_name in SWAP_PAIRS:
        assert once[a_name] is row[b_name] or once[a_name] == row[b_name] or (
            once[a_name] != once[a_name] and row[b_name] != row[b_name]), (
            f"{a_name}/{b_name} declared SWAP but did not exchange")

    return True
