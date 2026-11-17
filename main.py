"""Launch-day Twitch viewer forecast: an analog (nearest-launch) model in log-ratio space.

For one case the program knows hour 1's print p1 and the category's history up to
the end of hour 1.  Every comparable launch gives a full release-day curve, which is
turned into log ratios  y_h = ln(viewers_h / viewers_1)  for hours 2..24.  The
comparables are weighted by how much their situation at the end of hour 1 looked like
the target's (size of hour 1, how far hour 1 sits below the previous day's high, the
short trend into hour 1, what kind of launch it was), and the forecast for hour h is
a normal distribution in log space around the weighted mean of y_h, tilted by a damped
regression on the size of hour 1 (a bigger first hour leaves less room to grow), whose
spread is the weighted scatter of the analogs around that fit, widened for the small
sample.  Comparables are first lined up on the local clock (summer/winter time).

Standard library only, deterministic, no network.
"""

import json
import math
import os
import sys
from pathlib import Path

IN_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

HOURS = list(range(2, 25))
# Standard normal quantiles of the five scored levels.
ZS = (-1.6449, -0.6745, 0.0, 0.6745, 1.6449)

# Kernel bandwidths (natural-log units) of the similarity features.
BW_SIZE = 1.6     # ln(hour-1 viewers)
BW_DROP = 1.2     # ln(hour 1 / highest hour of the previous 24)
BW_TREND = 0.8    # ln(hour 1 / mean of the 3 hours before it)
BW_SURGE = 1.5    # ln(hour 1 / best hour of days -3 and -2), capped at SURGE_CAP
SURGE_CAP = 4.0
CLOCK_ALIGN = 1   # 1: shift comparables by an hour across the summer/winter clock change
CONSOLE_BONUS = 6.0  # prior weight of a console-only launch
ROLLING_BONUS = 1.5  # prior weight of a launch whose unlock is known to be midnight local
SLOPE_ON = 2      # tilt each hour's center by a regression: 1 on the drop, 2 on the size, 0 none
RIDGE = 0.5       # damping of that slope
SLOPE_MIN = -0.5  # steepest size slope allowed (log ratio per log viewer)
REACH = 0.0       # how far past the largest analog the size may be extrapolated (log units)
BACKGROUND = 0.03  # share of weight every usable comparable keeps regardless of distance
SPREAD_SCALE = 0.75  # multiplies the analog scatter
SPREAD_FLOOR = 0.07  # least log spread of any hour
TAIL_STRETCH = 1.1  # extra width of the 5% and 95% quantiles (fat tails)


def parse_time(text):
    """Seconds since 1970 of an ISO 8601 UTC instant such as 2026-11-19T00:00:00Z."""
    text = text.strip().replace("Z", "")
    date, _, clock = text.partition("T")
    y, m, d = (int(x) for x in date.split("-"))
    parts = clock.split(":") if clock else ["0", "0", "0"]
    hh = int(parts[0])
    mm = int(parts[1]) if len(parts) > 1 else 0
    ss = float(parts[2]) if len(parts) > 2 else 0.0
    # Days from civil (proleptic Gregorian), the usual closed form.
    y2 = y - (1 if m <= 2 else 0)
    era = y2 // 400
    yoe = y2 - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return days * 86400 + hh * 3600 + mm * 60 + ss


def summer_time(day_start):
    """1 when US daylight time is in force on that date (second Sunday of March up to the
    first Sunday of November), else 0.  Midnight on the US east coast is 04:00 UTC in
    summer and 05:00 UTC in winter, and Europe's clocks move within days of the US's, so a
    launch's whole daily rhythm sits one UTC hour earlier in summer."""
    date = day_start.strip().partition("T")[0]
    y, m, d = (int(x) for x in date.split("-"))

    def first_sunday(month):
        day1 = int(parse_time("%04d-%02d-01T00:00:00Z" % (y, month)) // 86400)
        weekday = (day1 + 4) % 7  # 0 = Sunday (1970-01-01 was a Thursday)
        return 1 + (7 - weekday) % 7

    start = (3, first_sunday(3) + 7)
    end = (11, first_sunday(11))
    return 1 if start <= (m, d) < end else 0


def hourly_index(rows, day_start, shift=0):
    """{k: viewers} where k = 0 is the release day's first hour, k = -1 the hour before.

    shift moves the day's start `shift` hours earlier, to line a summer launch's clock
    up with a winter target's (and the other way round with shift = -1)."""
    t0 = parse_time(day_start) - 3600.0 * shift
    series = {}
    for row in rows or []:
        v = row.get("viewers")
        if v is None:
            continue
        k = int(math.floor((parse_time(row["from"]) - t0) / 3600.0 + 1e-9))
        series[k] = float(v)
    return series


def situation(series, p1):
    """The similarity features of a launch at the end of its hour 1."""
    before = [series[k] for k in range(-24, 0) if k in series and series[k] > 0]
    near = [series[k] for k in range(-3, 0) if k in series and series[k] > 0]
    older = [series[k] for k in range(-72, -24) if k in series]
    size = math.log(max(p1, 1.0))
    drop = math.log(max(p1, 1.0) / max(before)) if before else None
    trend = math.log(max(p1, 1.0) / (sum(near) / len(near))) if near else None
    # How fresh the launch is: hour 1 against the best hour of the two days before the last one.
    # A rolling-midnight launch sits far above it; an early-access launch does not.
    surge = math.log(max(p1, 1.0) / max(max(older), 1.0)) if older else None
    if surge is not None:
        surge = max(-1.0, min(SURGE_CAP, surge))
    return size, drop, trend, surge


def launch_prior(comp):
    """How good an analogue a comparable is before looking at its numbers."""
    text = (comp.get("unlock") or "")
    low = text.lower()
    if "target-history" in low or "not a launch" in low:
        return 0.0
    w = 1.0
    if "tier: weak" in low:
        w *= 0.3
    if "early-access-contaminated" in low:
        w *= 0.5
    if console_only(comp):
        w *= CONSOLE_BONUS  # console-only, like every case of this contest
    if "rolling" in low or "midnight local" in low:
        w *= ROLLING_BONUS
    return w


# The contest's own practice launches: console games that unlocked at midnight local time.
KNOWN_CONSOLE = ("wolverine", "red dead redemption 2", "god of war ragnar", "spider-man 2")


def console_only(comp):
    name = (comp.get("name") or "").lower()
    if any(key in name for key in KNOWN_CONSOLE):
        return True
    platforms = (comp.get("platforms") or "").upper()
    # Drop bracketed remarks such as "(PC 2019-11)": a later port does not count.
    plain = ""
    depth = 0
    for ch in platforms:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            plain += ch
    tokens = [t.strip() for t in plain.replace("/", ",").split(",")]
    return not any(t == "PC" or t.startswith("PC ") or t == "STADIA" for t in tokens)


def sq(a, b, bw):
    if a is None or b is None:
        return 0.5  # unknown feature: a mild, fixed penalty
    return ((a - b) / bw) ** 2


def analogs(case):
    """[(weight, {hour: log ratio})] for the case's comparables."""
    known = [k for k in case["known"] if k["hour"] == 1]
    p1 = max(1.0, float(known[0]["viewers"]))
    target = hourly_index(case.get("history_hourly"), case["day_start"])
    t_size, t_drop, t_trend, t_surge = situation(target, p1)
    t_summer = summer_time(case["day_start"])
    out = []
    for comp in case.get("comparables") or []:
        prior = launch_prior(comp)
        if prior <= 0:
            continue
        shift = (summer_time(comp["day_start"]) - t_summer) if CLOCK_ALIGN else 0
        series = hourly_index(comp.get("hourly"), comp["day_start"], shift)
        c1 = series.get(0)
        if c1 is None or c1 < 1:
            continue
        c_size, c_drop, c_trend, c_surge = situation(series, c1)
        d2 = (sq(t_size, c_size, BW_SIZE) + sq(t_drop, c_drop, BW_DROP) + sq(t_trend, c_trend, BW_TREND)
              + sq(t_surge, c_surge, BW_SURGE))
        weight = prior * (math.exp(-0.5 * d2) + BACKGROUND)
        ratios = {}
        for h in HOURS:
            v = series.get(h - 1)
            if v is not None:
                ratios[h] = math.log(max(v, 1.0) / c1)
        if len(ratios) >= 12:
            out.append((weight, ratios, c_size if SLOPE_ON == 2 else c_drop))
    return p1, out, (t_size if SLOPE_ON == 2 else t_drop)


def fit_hour(pts, x_target):
    """Weighted mean of y, tilted by a ridge-damped slope on x (the drop below the previous
    day's high): pts is [(weight, y, x)].  Returns (center, variance, effective sample)."""
    sw = sum(w for w, _, _ in pts)
    mean = sum(w * y for w, y, _ in pts) / sw
    n_eff = sw * sw / sum(w * w for w, _, _ in pts)
    slope = 0.0
    xbar = 0.0
    usable = [(w, y, x) for w, y, x in pts if x is not None]
    if SLOPE_ON and x_target is not None and len(usable) >= 5:
        swx = sum(w for w, _, _ in usable)
        xbar = sum(w * x for w, _, x in usable) / swx
        ybar = sum(w * y for w, y, _ in usable) / swx
        sxx = sum(w * (x - xbar) ** 2 for w, _, x in usable) / swx
        sxy = sum(w * (x - xbar) * (y - ybar) for w, y, x in usable) / swx
        slope = sxy / (sxx + RIDGE)
        # On the drop: between "anchored on the old high" (-1) and "anchored on hour 1" (0).
        # On the size: a bigger hour 1 leaves less room to grow, never more.
        slope = max(-1.0 if SLOPE_ON == 1 else SLOPE_MIN, min(0.0, slope))
        lo = min(x for _, _, x in usable)
        hi = max(x for _, _, x in usable)
        x_target = max(lo, min(hi + REACH, x_target))  # barely extrapolate past the analogs
    center = mean + (slope * (x_target - xbar) if slope else 0.0)
    var = 0.0
    for w, y, x in pts:
        fitted = mean + (slope * (x - xbar) if (slope and x is not None) else 0.0)
        var += w * (y - fitted) ** 2
    var /= sw
    return center, var, n_eff


def smooth(values):
    """A 1-2-1 moving average along the hours, the ends kept."""
    n = len(values)
    res = []
    for i in range(n):
        lo = values[max(0, i - 1)]
        hi = values[min(n - 1, i + 1)]
        res.append(0.25 * lo + 0.5 * values[i] + 0.25 * hi)
    return res


def forecast(case):
    p1, pool, t_drop = analogs(case)
    centers = []
    spreads = []
    for h in HOURS:
        pts = [(w, r[h], x) for w, r, x in pool if h in r]
        sw = sum(w for w, _, _ in pts)
        if not pts or sw <= 0:
            centers.append(0.0)
            spreads.append(0.15 * math.sqrt(h - 1))
            continue
        mean, var, n_eff = fit_hour(pts, t_drop)
        # Small-sample widening: the mean itself is uncertain, and the variance is biased low.
        if n_eff > 1.5:
            var = var * n_eff / (n_eff - 1.0) * (1.0 + 1.0 / n_eff)
        else:
            var = max(var, (0.15 * math.sqrt(h - 1)) ** 2) * 3.0
        centers.append(mean)
        spreads.append(math.sqrt(var))
    spreads = smooth(spreads)
    rows = []
    for i, h in enumerate(HOURS):
        s = max(SPREAD_FLOOR, SPREAD_SCALE * spreads[i])
        qs = []
        for z in ZS:
            zz = z * TAIL_STRETCH if abs(z) > 1.0 else z
            qs.append(p1 * math.exp(centers[i] + zz * s))
        rows.append({"hour": h, "q": tidy(qs)})
    return {"hours": rows}


def tidy(qs):
    """Round to 2 decimals, keep inside (0, 1e9] and non-decreasing."""
    out = []
    prev = 0.01
    for q in qs:
        q = min(1e9, max(0.01, q))
        q = math.floor(q * 100.0 + 0.5) / 100.0
        q = max(q, prev)
        prev = q
        out.append(q)
    return out


def run():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(IN_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        try:
            result = forecast(case)
        except Exception:  # never leave a case without an output
            p1 = max(1.0, float(case["known"][0]["viewers"]))
            result = {"hours": [{"hour": h, "q": tidy([p1 * math.exp(z * 0.3 * math.sqrt(h - 1) / 2.0) for z in ZS])}
                                for h in HOURS]}
        text = json.dumps(result, separators=(",", ":")) + "\n"
        (OUT_DIR / (path.stem + ".out")).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    run()
