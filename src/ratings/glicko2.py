import math

TAU = 0.5
INITIAL_RATING = 1500
INITIAL_RD = 150
INITIAL_VOLATILITY = 0.06

# One Glicko-2 rating period, in days. Six months roughly matches UFC cadence:
# an active fighter competes two or three times a year, so about one bout per
# period. This is what converts a layoff into growing uncertainty.
RATING_PERIOD_DAYS = 182.5


class Glicko2Fighter:
    def __init__(self, rating=INITIAL_RATING, rd=INITIAL_RD, volatility=INITIAL_VOLATILITY):
        self.rating = rating
        self.rd = rd
        self.volatility = volatility

    def __repr__(self):
        return f"Glicko2Fighter(rating={self.rating:.1f}, rd={self.rd:.1f}, volatility={self.volatility:.4f})"


def _scale_down(fighter):
    mu = (fighter.rating - 1500) / 173.7178
    phi = fighter.rd / 173.7178
    return mu, phi


def _scale_up(mu, phi, volatility):
    rating = mu * 173.7178 + 1500
    rd = phi * 173.7178
    return rating, rd, volatility


def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi ** 2 / math.pi ** 2)


def _expected_score(mu, mu_j, phi_j):
    return 1 / (1 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _compute_v(mu, opponents):
    v = 0
    for mu_j, phi_j in opponents:
        e = _expected_score(mu, mu_j, phi_j)
        v += _g(phi_j) ** 2 * e * (1 - e)
    return 1 / v


def _compute_delta(mu, opponents, outcomes, v):
    delta = 0
    for (mu_j, phi_j), s in zip(opponents, outcomes):
        e = _expected_score(mu, mu_j, phi_j)
        delta += _g(phi_j) * (s - e)
    return v * delta


def _compute_new_volatility(phi, volatility, v, delta):
    a = math.log(volatility ** 2)
    tau = TAU

    def f(x):
        ex = math.exp(x)
        d2 = delta ** 2
        phi2 = phi ** 2
        return (ex * (d2 - phi2 - v - ex) / (2 * (phi2 + v + ex) ** 2)) - ((x - a) / tau ** 2)

    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    fa, fb = f(A), f(B)
    max_iter = 100
    iteration = 0
    while abs(B - A) > 1e-6 and iteration < max_iter:
        iteration += 1
        C = A + (A - B) * fa / (fb - fa)
        fc = f(C)
        if fc * fb <= 0:
            A, fa = B, fb
        else:
            fa /= 2
        B, fb = C, fc

    return math.exp(A / 2)


def update_ratings(fighter, opponent, outcome, periods_fighter=1.0, periods_opponent=1.0):
    """
    Update ratings for a single bout.
    outcome: 1.0 = fighter wins, 0.0 = fighter loses, 0.5 = draw

    periods_fighter / periods_opponent are the rating periods elapsed since
    each fighter last competed. Glicko-2 grows uncertainty with time away, so
    passing the real gap makes a comeback after years carry more uncertainty
    than a fight six weeks later. Defaults of 1.0 reproduce the old
    one-period-per-bout behaviour.

    Returns updated (fighter, opponent) as new Glicko2Fighter objects.
    """
    results = []

    for f, opp, s, periods in [
        (fighter, opponent, outcome, periods_fighter),
        (opponent, fighter, 1 - outcome, periods_opponent),
    ]:
        mu, phi = _scale_down(f)
        mu_j, phi_j = _scale_down(opp)

        opponents = [(mu_j, phi_j)]
        outcomes = [s]

        v = _compute_v(mu, opponents)
        delta = _compute_delta(mu, opponents, outcomes, v)
        new_volatility = _compute_new_volatility(phi, f.volatility, v, delta)

        # Uncertainty grows with elapsed time, not with fights played. Clamped
        # at the initial RD so a decade away cannot push a fighter beyond
        # "completely unknown".
        phi_star = math.sqrt(phi ** 2 + new_volatility ** 2 * max(periods, 0.0))
        phi_star = min(phi_star, INITIAL_RD / 173.7178)

        new_phi = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
        new_mu = mu + new_phi ** 2 * sum(
            _g(phi_j) * (s - _expected_score(mu, mu_j, phi_j))
            for (mu_j, phi_j), s in zip(opponents, outcomes)
        )

        new_rating, new_rd, new_vol = _scale_up(new_mu, new_phi, new_volatility)
        results.append(Glicko2Fighter(new_rating, new_rd, new_vol))

    return results[0], results[1]