"""Forward paper tests: rules, candle-by-candle resolution, isolation from the live variants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from app.db.models import ForwardExperimentTrade
from app.forward.experiments import TRACKS, ForwardExperiments, plans_for, resolve, series_of
from app.settings_store import RuntimeSettings

BAR = 15 * 60 * 10**9
T0 = int(datetime(2026, 11, 6, tzinfo=UTC).timestamp() * 1e9)


def frame(bars):
    """``bars``: (open, high, low, close). The signal is bar 0."""
    a = np.array(bars, dtype=float)
    t = T0 + BAR * np.arange(len(a), dtype=np.int64)
    return SimpleNamespace(open=a[:, 0], high=a[:, 1], low=a[:, 2], close=a[:, 3], open_time=t, close_time=t + BAR)


def setup(direction="LONG", strategy="breakout", mtf=True, status="QUALIFIED"):
    long = direction == "LONG"
    return SimpleNamespace(direction=direction, strategy=strategy, status=status, strategy_version="v1", atr=1.0, entry_reference=100.0,
                           stop_loss=97.0 if long else 103.0, take_profit=104.0 if long else 96.0, mtf_confirmed=mtf,
                           plan={"zone": {"lower": 98.0, "upper": 99.0} if long else {"lower": 101.0, "upper": 102.0}})


def rec(**kw):
    base = dict(direction="LONG", signal_ns=T0, stop_loss=90.0, legs=[[110.0, 1.0]], limit_price=None, through=None, max_bars=3, atr_pct=1.0, status="PENDING")
    return SimpleNamespace(**{**base, **kw})


def test_plans_are_symmetric_and_follow_the_locked_c_rule():
    tracks = {p["track"]: p for p in plans_for(setup())}
    assert set(tracks) == set(TRACKS)
    assert tracks["retest_touch"]["limit_price"] == 99.0 and tracks["retest_touch"]["through"] == 0.0
    assert tracks["retest_through"]["through"] == pytest.approx(0.1)
    assert tracks["htf_2r_control"]["stop_loss"] == 97.5 and tracks["htf_2r_control"]["legs"] == [[105.0, 1.0]]
    assert tracks["htf_30_40_30"]["legs"] == [[102.5, 0.3], [105.0, 0.4], [107.5, 0.3]]
    short = {p["track"]: p for p in plans_for(setup("SHORT"))}
    assert short["retest_touch"]["limit_price"] == 101.0
    assert short["htf_30_40_30"]["stop_loss"] == 102.5 and short["htf_30_40_30"]["legs"] == [[97.5, 0.3], [95.0, 0.4], [92.5, 0.3]]


def test_only_breakouts_retest_and_only_confirmed_signals_enter_the_exit_test():
    assert {p["track"] for p in plans_for(setup(strategy="pullback"))} == {"htf_2r_control", "htf_30_40_30"}
    assert {p["track"] for p in plans_for(setup(mtf=False))} == {"retest_touch", "retest_through"}
    assert plans_for(setup(strategy="pullback", mtf=None)) == []


def test_next_open_waits_opens_and_closes_without_future_candles():
    flat = (100, 100.5, 99.5, 100)
    t = rec()
    assert resolve(t, series_of(frame([flat]), "15m")) is None  # the entry bar doesn't exist yet
    opened = resolve(t, series_of(frame([flat, flat]), "15m"))
    assert opened["status"] == "OPEN" and "net_r" not in opened
    t.status = "OPEN"
    assert resolve(t, series_of(frame([flat, flat, flat]), "15m")) is None
    hit = resolve(t, series_of(frame([flat, flat, (100, 111, 100, 110)]), "15m"))
    assert hit["status"] == "CLOSED" and hit["exit_reason"] == "TARGET" and hit["gross_r"] == pytest.approx(1.0)
    assert hit["net_r"] < hit["gross_r"]  # costs are charged


def test_next_open_already_beyond_a_level_is_not_a_trade():
    assert resolve(rec(), series_of(frame([(100, 100, 100, 100), (112, 112, 111, 111)]), "15m"))["status"] == "UNFILLED"


def test_time_exit_needs_the_full_holding_period():
    flat = (100, 100.5, 99.5, 100)
    t = rec(status="OPEN")
    assert resolve(t, series_of(frame([flat] * 3), "15m")) is None
    assert resolve(t, series_of(frame([flat] * 4), "15m"))["exit_reason"] == "TIME_EXIT"


def test_stop_comes_first_when_a_candle_touches_both():
    flat = (100, 100.5, 99.5, 100)
    done = resolve(rec(), series_of(frame([flat, flat, (100, 111, 89, 100)]), "15m"))
    assert done["exit_reason"] == "AMBIGUOUS_STOP" and done["net_r"] < -1


def test_partial_exits_stay_open_until_every_leg_is_done():
    flat = (100, 100.5, 99.5, 100)
    t = rec(legs=[[105.0, 0.5], [110.0, 0.5]], status="OPEN", max_bars=10)
    assert resolve(t, series_of(frame([flat, flat, (100, 106, 100, 105)]), "15m")) is None
    done = resolve(t, series_of(frame([flat, flat, (100, 106, 100, 105), (105, 106, 89, 90)]), "15m"))
    assert done["targets_hit"] == 1 and done["gross_r"] == pytest.approx(0.5 * 0.5 + 0.5 * -1)


def test_retest_touch_and_through_differ_only_in_the_fill():
    sig = (100, 100.5, 99.5, 100)
    touch = (99.5, 99.8, 99.0, 99.5)  # reaches the 99 limit, not 0.1 below it
    away = (99.5, 100, 99.2, 100)
    limit = dict(limit_price=99.0, stop_loss=97.0, legs=[[103.0, 1.0]], max_bars=20)
    assert resolve(rec(**limit, through=0.0), series_of(frame([sig, touch]), "15m"))["status"] == "OPEN"
    t = rec(**limit, through=0.1)
    assert resolve(t, series_of(frame([sig, touch]), "15m")) is None  # could still fill
    assert resolve(t, series_of(frame([sig, touch] + [away] * 11), "15m"))["status"] == "NOT_FILLED"


def test_retest_is_cancelled_when_the_stop_trades_first():
    limit = dict(limit_price=99.0, stop_loss=97.0, legs=[[103.0, 1.0]], max_bars=20, through=0.0)
    sig = (100, 100.5, 99.5, 100)
    bars = [sig, (100, 100, 96, 97)] + [(98, 99.5, 98, 99)] * 11
    assert resolve(rec(**limit), series_of(frame(bars), "15m"))["status"] == "NOT_FILLED"


def test_history_that_no_longer_reaches_a_signal_expires():
    f = frame([(100, 100, 100, 100)] * 2)
    f.open_time = f.open_time + 100 * BAR
    assert resolve(rec(), series_of(f, "15m"))["status"] == "EXPIRED"


def tracker():
    from app.db.session import session_factory
    return ForwardExperiments(SimpleNamespace(current=RuntimeSettings()), session_factory)


async def rows():
    from sqlalchemy import select

    from app.db.session import session_factory
    async with session_factory()() as s:
        return (await s.execute(select(ForwardExperimentTrade))).scalars().all()


NOW = datetime.fromtimestamp((T0 + BAR) / 1e9, UTC) + timedelta(seconds=5)


async def test_records_once_and_counts_what_it_checked(db):
    x = tracker()
    f, ev = frame([(100, 100.5, 99.5, 100)]), SimpleNamespace(setup=setup())
    await x.on_evaluation("BTCUSDT", "15m", f, ev, True, NOW)
    await x.on_evaluation("BTCUSDT", "15m", f, ev, True, NOW)
    assert len(await rows()) == 4 and x.status["candles_checked"] == 2 and x.status["last_error"] is None


async def test_ignores_rejected_setups_and_bad_data_but_still_counts_the_candle(db):
    x = tracker()
    f = frame([(100, 100.5, 99.5, 100)])
    await x.on_evaluation("BTCUSDT", "15m", f, SimpleNamespace(setup=setup(status="REJECTED")), True, NOW)
    await x.on_evaluation("BTCUSDT", "15m", f, SimpleNamespace(setup=setup()), False, NOW)
    await x.on_evaluation("BTCUSDT", "15m", f, SimpleNamespace(setup=None), True, NOW)
    assert await rows() == [] and x.status["candles_checked"] == 3 and x.status["signals_seen"] == 0


async def test_a_late_detection_is_recorded_but_never_entered(db):
    x = tracker()
    f = frame([(100, 100.5, 99.5, 100)])
    await x.on_evaluation("BTCUSDT", "15m", f, SimpleNamespace(setup=setup()), True, NOW + timedelta(hours=2))
    assert {r.status for r in await rows()} == {"UNFILLED"}


async def test_failures_never_reach_the_scanner(db):
    x = tracker()
    await x.on_evaluation("BTCUSDT", "15m", None, SimpleNamespace(setup=None), True, NOW)
    assert x.status["last_error"]


async def test_report_is_unvalidated_and_pairs_the_exit_test_with_its_control(db):
    x = tracker()
    flat = (100, 100.5, 99.5, 100)
    ev = SimpleNamespace(setup=setup(strategy="pullback"))
    await x.on_evaluation("BTCUSDT", "15m", frame([flat]), ev, True, NOW)
    v = await x.report()  # scoped to the running strategy configuration, which isn't "v1"
    assert v["evidence"] == "Unvalidated" and v["other_version_records"] == 2 and all(t["signals"] == 0 for t in v["tracks"])

    ev =SimpleNamespace(setup=SimpleNamespace(**{**vars(setup(strategy="pullback")), "strategy_version": x.settings.current.strategy.version}))
    await x.on_evaluation("ETHUSDT", "15m", frame([flat]), ev, True, NOW)
    await x.on_evaluation("ETHUSDT", "15m", frame([flat, flat, (100, 120, 100, 120)]), SimpleNamespace(setup=None), True, NOW)
    tracks ={t["key"]: t for t in (await x.report())["tracks"]}
    assert tracks["htf_2r_control"]["closed"] == 1 and tracks["htf_30_40_30"]["closed"] == 1
    assert tracks["htf_30_40_30"]["vs_control"]["n"] == 1 and tracks["htf_30_40_30"]["enough_data"] is False


def test_does_not_import_or_touch_paper_trading_state():
    import ast

    import app.forward.experiments as m
    tree = ast.parse(open(m.__file__, encoding="utf-8").read())
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    names += [a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names]
    for banned in ("PaperTrade", "app.alerts.telegram", "app.notifications.service", "app.forward.service"):
        assert banned not in names
