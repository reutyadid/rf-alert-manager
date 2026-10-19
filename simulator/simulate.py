"""RF sensor traffic simulator for the RF Alert Manager.

    python simulator/simulate.py --url http://localhost --rate 5

Runtime uses the Python standard library only (no installs needed on any machine).
Scenarios are designed around the seeded rule `rule-ism-high-power`
(2400-2483.5 MHz, > -50 dBm, min_duration 10 s, max_gap 3 s, resolve_after 60 s):

  background  weak signals on random frequencies; never matches a rule        -> no alert
  emitter     2437.0 MHz every second, heard by 3 sensors (+1 weak, distant)  -> one alert opens
              after ~10 s, stays open; sensor_ids has 3 sensors
  burst       2412.0 MHz for 5 s (t=15..19)                                   -> no alert
  vanishing   2462.0 MHz from t=20 to t=59, then silent                       -> opens ~t=30,
              auto-resolves ~90 s after it stops (resolve_after 60 s + 30 s allowed lateness)
  late        at t=45, the emitter frequency from two new sensors: one 20 s late (accepted, joins
              the emitter alert) and one 40 s late (beyond the 30 s allowed lateness: stored by
              ingest, dropped by alert-service as late)

The emitter also keeps alert-service's event-time watermark moving. Without it, alerts still
resolve, later, through alert-service's idle fallback (IDLE_TIMEOUT_S of wall-clock time).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

ALL_SCENARIOS = ("background", "emitter", "burst", "vanishing", "late")

BACKGROUND_SENSORS = [f"sensor-{i:02d}" for i in range(1, 9)]
EMITTER_MHZ = 2437.0
EMITTER_SENSORS = {"sensor-01": -40.0, "sensor-02": -44.0, "sensor-03": -47.0}
EMITTER_WEAK_SENSOR = ("sensor-04", -70.0)  # far away: below threshold, must not break the run
# Stays inside one 0.1 MHz signal-key bucket: 2437.0 owns [2436.95, 2437.05).
EMITTER_JITTER_MHZ = 0.03
BURST_MHZ, BURST_TICKS = 2412.0, range(15, 20)
VANISHING_MHZ, VANISHING_TICKS = 2462.0, range(20, 60)
LATE_TICK = 45
LATE_OBSERVATIONS = {"sensor-late-20": 20, "sensor-late-40": 40}  # sensor -> seconds late
BASE_LOCATION = (32.08, 34.78)


# --- scenarios: pure functions of (tick, start time, rng) -------------------------------------


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def observation(
    sensor_id: str, ts: datetime, freq: float, power: float, rng: random.Random
) -> dict:
    lat, lon = BASE_LOCATION
    return {
        "sensor_id": sensor_id,
        "timestamp": iso(ts),
        "frequency_mhz": round(freq, 4),
        "bandwidth_khz": 20000,
        "power_dbm": round(power, 1),
        "location": {
            "lat": round(lat + rng.uniform(-0.05, 0.05), 5),
            "lon": round(lon + rng.uniform(-0.05, 0.05), 5),
        },
    }


def background(n: int, ts: datetime, rng: random.Random, rate: float) -> list[dict]:
    count = int(rate) + (1 if rng.random() < rate - int(rate) else 0)
    return [
        observation(
            rng.choice(BACKGROUND_SENSORS), ts, rng.uniform(100, 6000), rng.uniform(-95, -65), rng
        )
        for _ in range(count)
    ]


def emitter(n: int, ts: datetime, rng: random.Random) -> list[dict]:
    obs = [
        observation(
            sensor,
            ts,
            EMITTER_MHZ + rng.uniform(-EMITTER_JITTER_MHZ, EMITTER_JITTER_MHZ),
            power + rng.uniform(-1.5, 1.5),
            rng,
        )
        for sensor, power in EMITTER_SENSORS.items()
    ]
    weak_sensor, weak_power = EMITTER_WEAK_SENSOR
    obs.append(observation(weak_sensor, ts, EMITTER_MHZ, weak_power + rng.uniform(-2, 2), rng))
    return obs


def burst(n: int, ts: datetime, rng: random.Random) -> list[dict]:
    return [observation("sensor-05", ts, BURST_MHZ, -38.0, rng)] if n in BURST_TICKS else []


def vanishing(n: int, ts: datetime, rng: random.Random) -> list[dict]:
    if n not in VANISHING_TICKS:
        return []
    return [observation("sensor-06", ts, VANISHING_MHZ, -42.0, rng)]


def late(n: int, ts: datetime, rng: random.Random) -> list[dict]:
    if n != LATE_TICK:
        return []
    return [
        observation(sensor, ts - timedelta(seconds=delay), EMITTER_MHZ, -41.0, rng)
        for sensor, delay in LATE_OBSERVATIONS.items()
    ]


def observations_for_tick(
    n: int, start: datetime, rng: random.Random, scenarios: set[str], rate: float
) -> list[dict]:
    """All observations for second n. Event time is start + n seconds."""
    ts = start + timedelta(seconds=n)
    obs: list[dict] = []
    if "background" in scenarios:
        obs += background(n, ts, rng, rate)
    if "emitter" in scenarios:
        obs += emitter(n, ts, rng)
    if "burst" in scenarios:
        obs += burst(n, ts, rng)
    if "vanishing" in scenarios:
        obs += vanishing(n, ts, rng)
    if "late" in scenarios:
        obs += late(n, ts, rng)
    return obs


def announcements(n: int, scenarios: set[str]) -> list[str]:
    notes = {
        ("emitter", 0): "emitter ON 2437.0 MHz (3 sensors + 1 weak): expect an alert after ~10 s",
        ("burst", BURST_TICKS.start): "burst ON 2412.0 MHz for 5 s: expect NO alert",
        ("burst", BURST_TICKS.stop): "burst OFF",
        ("vanishing", VANISHING_TICKS.start): "vanishing ON 2462.0 MHz: expect an alert at ~t=30",
        ("vanishing", VANISHING_TICKS.stop): (
            "vanishing OFF: expect auto-resolve ~90 s from now "
            "(resolve_after 60 s + allowed lateness 30 s)"
        ),
        ("late", LATE_TICK): (
            "late: sensor-late-20 (20 s late, joins the emitter alert) and "
            "sensor-late-40 (40 s late, dropped by alert-service as late)"
        ),
    }
    return [msg for (scenario, tick), msg in notes.items() if tick == n and scenario in scenarios]


def say(line: str) -> None:
    print(line, flush=True)  # flush so piped output (e.g. | tee) is live


# --- HTTP (stdlib) ---------------------------------------------------------------------------

Post = Callable[[str, bytes], tuple[int | None, dict]]


def http_post(url: str, body: bytes) -> tuple[int | None, dict]:
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    return _send(request)


def http_get(url: str) -> tuple[int | None, dict | list]:
    return _send(urllib.request.Request(url, method="GET"))


def _send(request: urllib.request.Request) -> tuple[int | None, dict | list]:
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except json.JSONDecodeError:
            return err.code, {}
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as err:
        return None, {"error": str(err)}


def send_batch(
    post: Post,
    url: str,
    body: bytes,
    max_attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int | None, dict]:
    """POST one batch; on 503 or a connection error, resend the exact same bytes.

    Resending the same body (same sensor_id/timestamp/frequency) is what makes the retry safe:
    ingest skips rows that are already stored, and republishes nothing for them.
    """
    status, payload = None, {}
    for attempt in range(max_attempts):
        status, payload = post(url, body)
        if status is not None and status != 503:
            return status, payload
        if attempt < max_attempts - 1:
            sleep(min(0.5 * 2**attempt, 4.0))
    return status, payload


# --- main loop -------------------------------------------------------------------------------


def run(
    url: str,
    duration: int,
    scenarios: set[str],
    rate: float,
    seed: int | None,
    watch: bool,
    post: Post = http_post,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    out: Callable[[str], None] = say,
) -> None:
    rng = random.Random(seed)
    start_wall = now().replace(microsecond=0)
    start_mono = monotonic()
    endpoint = f"{url.rstrip('/')}/api/v1/observations"
    n = 0
    while duration == 0 or n < duration:
        # Schedule against start + n (not sleep(1) after work) so ticks do not drift.
        delay = start_mono + n - monotonic()
        if delay > 0:
            sleep(delay)
        for note in announcements(n, scenarios):
            out(f"t={n}s  >> {note}")
        batch = observations_for_tick(n, start_wall, rng, scenarios, rate)
        if batch:
            body = json.dumps(batch).encode()  # encoded once; retries resend these bytes
            status, payload = send_batch(post, endpoint, body, sleep=sleep)
            if status == 200:
                out(
                    f"t={n}s  sent={len(batch)} accepted={payload.get('accepted')} "
                    f"dup={payload.get('duplicates')}"
                )
            else:
                out(f"t={n}s  sent={len(batch)} FAILED status={status} {payload}")
        if watch and n % 10 == 0 and n > 0:
            print_alerts(url, out)
        n += 1


def check_rules(url: str, out: Callable[[str], None] = say) -> None:
    status, rules = http_get(f"{url.rstrip('/')}/api/v1/rules")
    if status != 200 or not isinstance(rules, list):
        out(f"warning: could not read rules (status={status}); is ingest-service up?")
        return
    by_id = {r.get("rule_id"): r for r in rules}
    rule = by_id.get("rule-ism-high-power")
    if rule is None or not rule.get("enabled"):
        out("warning: rule-ism-high-power is missing or disabled: no alert will open")


def print_alerts(url: str, out: Callable[[str], None] = say) -> None:
    status, alerts = http_get(f"{url.rstrip('/')}/api/v1/alerts")
    if status != 200 or not isinstance(alerts, list):
        out(f"   [alerts] not available (status={status})")
        return
    if not alerts:
        out("   [alerts] none")
    for a in alerts:
        out(
            f"   [alerts] {a.get('state'):<12} {a.get('rule_id')} "
            f"{a.get('frequency_mhz')} MHz sensors={a.get('sensor_ids')} "
            f"occurrences={a.get('occurrences')}"
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://localhost", help="base URL (nginx)")
    parser.add_argument(
        "--rate", type=float, default=5.0, help="background observations per second"
    )
    parser.add_argument("--duration", type=int, default=240, help="seconds; 0 = forever")
    parser.add_argument(
        "--scenarios",
        default="all",
        help=f"comma-separated subset of {','.join(ALL_SCENARIOS)} (default: all)",
    )
    parser.add_argument("--seed", type=int, default=None, help="random seed for repeatable runs")
    parser.add_argument("--watch", action="store_true", help="print alerts every 10 s")
    args = parser.parse_args(argv)
    if args.scenarios == "all":
        args.scenarios = set(ALL_SCENARIOS)
    else:
        args.scenarios = {s.strip() for s in args.scenarios.split(",") if s.strip()}
        unknown = args.scenarios - set(ALL_SCENARIOS)
        if unknown:
            parser.error(f"unknown scenarios: {', '.join(sorted(unknown))}")
    if args.rate < 0 or math.isnan(args.rate):
        parser.error("--rate must be >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if "late" in args.scenarios and "emitter" not in args.scenarios:
        say("warning: 'late' needs 'emitter' running to keep the watermark near now")
    check_rules(args.url)
    try:
        run(args.url, args.duration, args.scenarios, args.rate, args.seed, args.watch)
    except KeyboardInterrupt:
        say("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
