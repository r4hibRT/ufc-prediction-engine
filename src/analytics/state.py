"""Per-fighter state accumulated while replaying bout history in order.

Every value here describes what was true BEFORE the bout being processed --
`update()` is called only after a snapshot has been taken. That ordering is
what makes the point-in-time statistics honest.
"""

RECENT_FORM_WINDOW = 5


def fight_seconds(round_, time_str):
    """Elapsed fight time: five minutes per completed round plus the last."""
    try:
        completed = (int(round_) - 1) * 300
        mins, secs = time_str.split(":")
        return completed + int(mins) * 60 + int(secs)
    except (TypeError, ValueError, AttributeError):
        return None


class FighterState:
    """What a fighter had done before the bout currently being processed."""

    def __init__(self):
        self.bouts = 0          # results that stood
        self.appearances = 0    # times in the cage, no contests included
        self.wins = 0
        self.win_streak = 0
        self.loss_streak = 0
        self.recent = []
        self.decision_count = 0

        self.ko_losses = 0
        self.sub_losses = 0
        self.finishes = 0

        self.career_seconds = 0
        self.bout_dates = []
        self.division_bouts = {}
        self.division_first_date = {}
        self.last_bout_date = None

        self.rating = None
        self.rd = None
        self.volatility = None
        self.peak_rating = None
        self.peak_date = None

        self.opponent_ratings = []

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

    @property
    def has_rating(self):
        return self.rating is not None

    @property
    def recent_form_5(self):
        window = self.recent[-RECENT_FORM_WINDOW:]
        return sum(window) / len(window) if window else None

    def layoff(self, bout_date):
        if self.last_bout_date is None:
            return None
        return (bout_date - self.last_bout_date).days

    @property
    def avg_opponent_rating(self):
        if not self.opponent_ratings:
            return None
        return sum(self.opponent_ratings) / len(self.opponent_ratings)

    @property
    def max_opponent_rating(self):
        return max(self.opponent_ratings) if self.opponent_ratings else None

    def snapshot(self, bout_date):
        """This fighter's state entering a bout, as a bout_snapshots row."""
        return {
            "bouts_before": self.bouts,
            "appearances_before": self.appearances,
            "wins_before": self.wins,
            "win_streak": self.win_streak,
            "loss_streak": self.loss_streak,
            "recent_form_5": self.recent_form_5,
            "rating_before": self.rating,
            "rd_before": self.rd,
            "peak_rating_before": self.peak_rating,
            "layoff_days": self.layoff(bout_date),
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
            "avg_opponent_rating": self.avg_opponent_rating,
            "max_opponent_rating": self.max_opponent_rating,
        }

    def update(self, *, date, weight_class, result, method, seconds,
               rating, rd, volatility, opponent_prior_rating, stats, opp_stats,
               counts_result=True):
        """Advance state after a bout. A no contest still accumulates cage time,
        statistics and activity, but leaves the win-loss record untouched."""
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
        if result == 1 and method in ("KO/TKO", "Submission"):
            self.finishes += 1
        if result == 0:
            if method == "KO/TKO":
                self.ko_losses += 1
            elif method == "Submission":
                self.sub_losses += 1

        if rating is not None:
            self.rating = rating
            self.rd = rd
            self.volatility = volatility
            if self.peak_rating is None or rating > self.peak_rating:
                self.peak_rating = rating
                self.peak_date = date
