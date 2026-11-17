# launch-day-ccu-analogs

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/cryptoresetlife/launch-day-ccu-analogs/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/cryptoresetlife/launch-day-ccu-analogs/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts how many people watch a big game launch on Twitch in each hour of its
release day, from the first hour's count: five quantiles (5/25/50/75/95%) for hours
2 to 24. Built for Grand Theft Auto VI's release day, 2026-11-19 (UTC).

## How it works

`main.py` is an analog model in log-ratio space, standard library only:

1. Every past launch in the case file becomes a curve of `ln(viewers_h / viewers_1)`.
2. Past launches are lined up on the local clock: a launch from the summer-time half of
   the year is shifted one UTC hour, because midnight on the US east coast (when a
   rolling-midnight console launch unlocks there) is 04:00 UTC in summer and 05:00 UTC
   in winter.
3. Each one is weighted by how much its situation at the end of hour 1 resembled the
   target's: the size of hour 1, how far it sits below the previous day's high, the
   trend into it, how far the launch has surged above the days before, and whether it
   was a console-only, midnight-local launch.
4. The forecast for an hour is a normal distribution in log space around the weighted
   mean ratio, as wide as the weighted scatter of the analogs (widened for the small
   sample), calibrated by leaving each past launch out in turn.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>
