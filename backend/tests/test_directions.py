"""LONG and SHORT are implemented symmetrically end to end. Most tests reflect real-shaped
synthetic prices (p -> M - p), so a rally becomes an identical fall, and require the mirrored
decision. No test forces a signal in either direction. Synthetic test data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.alerts.telegram import SendResult, format_signal_message
from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.metrics import compute_metrics
from app.forward.costs import Quote, check_exit, entry_fill, exit_fill, settle, trigger_price
from app.forward.settings import PaperCosts
from app.forward.variants import plan_for
from app.notifications.events import EventIn
from app.research.direction_audit import mirror_constant, mirror_frame
from app.risk.sizing import SizingConfig, liquidation_price, size_position
from app.risk.stops import compute_stop
from app.risk.targets import compute_targets
from app.settings_store import RuntimeSettings
from app.strategy.engine import evaluate_at, htf_states_at
from app.strategy.features import compute_features
from app.strategy.params import StrategyParams
from app.timeutil import utcnow
from tests.conftest import make_candles, phase1_params

FLIP = {"LONG": "SHORT", "SHORT": "LONG"}
NEUTRAL = SizingConfig(risk_pct=0.001, fee_rate_pct=0, slippage_pct=0, leverage=1)


def trending(drift: float, seed: int = 3, n: int = 1200) -> pd.DataFrame:
    return make_candles(n, seed=seed, drift=drift)


def decisions(f, p, htf=None, sizing=NEUTRAL):
    out = {}
    for i in range(p.warmup_bars, len(f)):
        ev = evaluate_at(f, i, p, htf or {}, sizing, None, False)
        if ev.setup is not None:
            out[i] = ev.setup
    return out


# ----------------------------------------------------------------------------- detection

def test_mirrored_prices_give_mirrored_decisions_and_levels():
    """Without the zone detector (see the next test) the logic is an exact mirror image."""
    p = phase1_params(mtf={"enabled": False})
    df = trending(0.0008)
    m = mirror_constant([df])
    a = decisions(compute_features(df, "15m", p), p)
    b = decisions(compute_features(mirror_frame(df, m), "15m", p), p)
    assert a and set(a) == set(b)
    for i, st in a.items():
        o = b[i]
        assert o.direction == FLIP[st.direction] and o.strategy == st.strategy and o.status == st.status
        for x, y in ((st.entry_reference, o.entry_reference), (st.stop_loss, o.stop_loss), (st.take_profit, o.take_profit)):
            assert x + y == pytest.approx(m, rel=1e-6)


def _neutral_codes(st) -> list[str]:
    """Reason codes with their direction words removed: ``failed_rsi_in_long_band`` is the
    mirror of ``failed_rsi_in_short_band``."""
    swap = {"long": "dir", "short": "dir", "positive": "sign", "negative": "sign"}
    return sorted("_".join(swap.get(w, w) for w in r["code"].split("_")) for r in st.reasons)


def test_zone_based_strategy_is_a_mirror_image_when_the_zones_are():
    """The production rules, with zones reflected rather than re-detected: this isolates the zone
    detector, whose clustering scans prices from low to high (documented in DIRECTION_AUDIT.md)."""
    from app.research.direction_audit import reflect_zone
    from app.strategy import engine as eng

    p = StrategyParams(mtf={"enabled": False})
    df = make_candles(1400, seed=11, drift=0.0002, vol=0.006)
    m = mirror_constant([df])
    f, fm = compute_features(df, "15m", p), compute_features(mirror_frame(df, m), "15m", p)
    real = eng.zones_at
    original = {}
    mirrored = {}
    for i in range(p.warmup_bars, len(f)):
        ev = evaluate_at(f, i, p, {}, NEUTRAL, None, False)
        original.update({(i, st.direction, st.strategy): st for st in ev.setups if st.status != "WATCHLIST"})
    eng.zones_at = lambda ff, i, pp, sym=None, mx=None: [reflect_zone(z, m) for z in real(f, i, pp, sym, mx)]
    try:
        for i in range(p.warmup_bars, len(fm)):
            ev = evaluate_at(fm, i, p, {}, NEUTRAL, None, False)
            mirrored.update({(i, FLIP[st.direction], st.strategy): st for st in ev.setups if st.status != "WATCHLIST"})
    finally:
        eng.zones_at = real
    assert original and set(original) == set(mirrored)
    for k, st in original.items():
        assert mirrored[k].status == st.status
        assert _neutral_codes(mirrored[k]) == _neutral_codes(st)


def test_each_trend_produces_signals_in_its_own_direction_only():
    p = phase1_params(mtf={"enabled": False})
    up = decisions(compute_features(trending(0.0008), "15m", p), p, sizing=None)
    down = decisions(compute_features(trending(-0.0008), "15m", p), p, sizing=None)
    assert up and {s.direction for s in up.values()} == {"LONG"}
    assert down and {s.direction for s in down.values()} == {"SHORT"}


def test_a_trend_stack_is_required_in_the_signal_direction():
    p = phase1_params(mtf={"enabled": False})
    f = compute_features(trending(0.0008), "15m", p)
    for i, st in decisions(f, p, sizing=None).items():
        assert f.ema_fast[i] > f.ema_mid[i] > f.ema_slow[i] and st.direction == "LONG"


def test_market_structure_is_recognised_both_ways():
    p = StrategyParams()
    up = compute_features(trending(0.0012, n=600), "15m", p).structure.structure
    down = compute_features(trending(-0.0012, n=600), "15m", p).structure.structure
    assert (up == 1).sum() > (up == -1).sum()
    assert (down == -1).sum() > (down == 1).sum()
    mirrored = compute_features(mirror_frame(trending(0.0012, n=600), 400.0), "15m", p).structure.structure
    assert (mirrored == -up).mean() > 0.99


def test_higher_timeframes_confirm_and_disagree_in_both_directions():
    p = StrategyParams()
    up = {"1h": compute_features(trending(0.0012, n=500), "1h", p)}
    down = {"1h": compute_features(trending(-0.0012, n=500), "1h", p)}
    at = int(up["1h"].close_time[-1])
    assert htf_states_at(up, at, "15m", p)[0].trend == "BULLISH"
    assert htf_states_at(down, at, "15m", p)[0].trend == "BEARISH"
    q = phase1_params()  # multi-timeframe on, quality mode
    f = compute_features(trending(0.0008), "15m", q)
    fs = compute_features(trending(-0.0008), "15m", q)
    i = next(iter(decisions(f, q, sizing=None)))
    j = next(iter(decisions(fs, q, sizing=None)))
    same = {"1h": up["1h"], "4h": compute_features(trending(0.0012, n=500), "4h", q)}
    opposite = {"1h": down["1h"], "4h": compute_features(trending(-0.0012, n=500), "4h", q)}
    assert evaluate_at(f, i, q, same, context=False).setup.mtf_confirmed is True
    assert evaluate_at(f, i, q, opposite, context=False).setup.mtf_confirmed is False
    assert evaluate_at(fs, j, q, opposite, context=False).setup.mtf_confirmed is True
    assert evaluate_at(fs, j, q, same, context=False).setup.mtf_confirmed is False


# ----------------------------------------------------------------------------- levels and sizing

@pytest.mark.parametrize("direction,sign", [("LONG", -1), ("SHORT", 1)])
def test_stop_and_targets_sit_on_the_correct_side(direction, sign):
    stop = compute_stop(direction, 100, 2, "atr", None, 1.5)
    assert stop.price == pytest.approx(100 + sign * 3)
    targets = compute_targets(direction, 100, stop.distance, [1, 2, 3], [30, 40, 30])
    assert [t.price for t in targets] == pytest.approx([100 - sign * 3, 100 - sign * 6, 100 - sign * 9])
    struct = compute_stop(direction, 100, 2, "hybrid", 100 + sign * 4, 1.5, 0.25)
    assert struct.price == pytest.approx(100 + sign * 4.5)
    # a structural level on the wrong side is ignored rather than used
    assert compute_stop(direction, 100, 2, "hybrid", 100 - sign * 4, 1.5, 0.25).method == "atr"


@pytest.mark.parametrize("direction,stop,target", [("LONG", 95, 110), ("SHORT", 105, 90)])
def test_position_sizing_keeps_the_same_risk_in_both_directions(direction, stop, target):
    cfg = SizingConfig(account_balance=10_000, risk_pct=1, fee_rate_pct=0.055, slippage_pct=0.02, leverage=5)
    r = size_position(direction, 100, stop, cfg, None, [("TP", target, 2, 100)])
    assert r.estimated_loss == pytest.approx(100, rel=1e-6) and r.feasible
    assert r.targets[0].net_rr > 0
    side = -1 if direction == "LONG" else 1
    assert (r.liquidation_price - 100) * side > 0  # liquidation is on the stop side of the entry


def test_invalid_stop_side_is_refused_and_liquidation_is_mirrored():
    with pytest.raises(ValueError):
        size_position("LONG", 100, 105, SizingConfig())
    with pytest.raises(ValueError):
        size_position("SHORT", 100, 95, SizingConfig())
    assert liquidation_price("LONG", 100, 5, 0.005) == pytest.approx(80.5)
    assert liquidation_price("SHORT", 100, 5, 0.005) == pytest.approx(119.5)


def test_short_target_below_zero_is_rejected_without_affecting_longs():
    with pytest.raises(ValueError):
        compute_targets("SHORT", 1, 0.5, [1, 2, 3], [30, 40, 30])
    assert compute_targets("LONG", 1, 0.5, [1, 2, 3], [30, 40, 30])[2].price == pytest.approx(2.5)


# ----------------------------------------------------------------------------- paper trading

class _Setup:
    def __init__(self, direction, mtf=True):
        self.direction, self.mtf_confirmed = direction, mtf
        s = -1 if direction == "LONG" else 1
        self.entry_reference, self.atr = 100.0, 2.0
        self.stop_loss, self.take_profit = 100.0 + s * 2, 100.0 - s * 4


@pytest.mark.parametrize("direction,stop,target", [("LONG", 95.0, 110.0), ("SHORT", 105.0, 90.0)])
def test_variants_build_mirrored_plans(direction, stop, target):
    assert plan_for("B", _Setup(direction)).stop == pytest.approx(stop)
    assert plan_for("B", _Setup(direction)).target == pytest.approx(target)
    assert plan_for("C", _Setup(direction, mtf=True)).accepted
    assert not plan_for("C", _Setup(direction, mtf=False)).accepted
    assert plan_for("A", _Setup(direction)).stop == _Setup(direction).stop_loss


@pytest.mark.parametrize("direction,fill_side,close_side,hit,miss", [
    ("LONG", 100.1, 99.9, 94.0, 111.0),
    ("SHORT", 99.9, 100.1, 106.0, 89.0),
])
def test_costs_are_always_adverse_and_exits_mirror(direction, fill_side, close_side, hit, miss):
    pc = PaperCosts(slippage_pct=0, stop_slippage_pct=0.05)
    q = Quote(99.9, 100.1, 100.0, 1.0)
    assert entry_fill(direction, q, pc).price == pytest.approx(fill_side)  # buys the ask, sells the bid
    assert trigger_price(direction, q, pc)[0] == pytest.approx(close_side)
    stop, target = (95.0, 110.0) if direction == "LONG" else (105.0, 90.0)
    assert check_exit(direction, hit, stop, target) == "STOP"
    assert check_exit(direction, miss, stop, target) == "TARGET"
    worse = exit_fill(direction, "STOP", stop, target, pc)
    assert (worse.price < stop) if direction == "LONG" else (worse.price > stop)


@pytest.mark.parametrize("direction,fill,stop,target", [("LONG", 100.0, 95.0, 110.0), ("SHORT", 100.0, 105.0, 90.0)])
def test_paper_settlement_has_the_right_sign(direction, fill, stop, target):
    pc = PaperCosts()
    win = settle(direction, 2.0, fill, stop, target, "TARGET", 0.0, pc)
    loss = settle(direction, 2.0, fill, stop, stop, "STOP", 0.0, pc)
    assert win["gross_pnl"] == pytest.approx(20) and win["net_pnl"] > 0 and win["net_r"] > 1.9
    assert loss["gross_pnl"] == pytest.approx(-10) and loss["net_r"] < -1.0
    # positive funding is a cost passed in by the tracker; the same cost lowers either direction
    assert settle(direction, 2.0, fill, stop, target, "TARGET", 1.0, pc)["net_pnl"] == pytest.approx(win["net_pnl"] - 1)


class _Store:
    def __init__(self, s):
        self.current = s


class _Hub:
    async def broadcast(self, event, data):
        pass


class _Telegram:
    def __init__(self):
        self.messages = []

    async def send_message(self, text):
        self.messages.append(text)
        return SendResult(True, 1)


class _State:
    view = {}

    def ticker_view(self, symbol):
        return self.view


class _Market:
    exchange = "bybit"

    def __init__(self):
        self.state = _State()


def _frame(direction):
    p = phase1_params(mtf={"enabled": False})
    df = make_candles(1200, seed=3, drift=0.0008 if direction == "LONG" else -0.0008)
    f = compute_features(df, "15m", p)
    for i in range(p.warmup_bars, len(f) - 40):
        ev = evaluate_at(f, i, p, {}, context=False)
        if ev.setup is not None:
            assert ev.setup.direction == direction
            f2 = compute_features(df.iloc[: i + 1], "15m", p)
            return p, f2, i, evaluate_at(f2, i, p, {}, context=False)
    raise AssertionError("no setup")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
async def test_paper_trades_and_notifications_work_in_both_directions(db, direction):
    from sqlalchemy import select

    from app.db.models import NotificationEvent, PaperTrade
    from app.db.session import session_factory
    from app.forward.service import ForwardService
    from app.notifications.service import NotificationService

    p, f, i, ev = _frame(direction)
    s = RuntimeSettings(strategy=p)
    tg = _Telegram()
    center = NotificationService(session_factory, _Hub(), _Store(s), tg)
    m = _Market()
    px = float(f.close[i])
    m.state.view = {"available": True, "bid_price": px * 0.9999, "ask_price": px * 1.0001, "last_price": px,
                    "quote_at": utcnow().isoformat(), "funding_rate": 0.0001}
    svc = ForwardService(m, _Store(s), session_factory)
    svc.center = center
    close = datetime.fromtimestamp(int(f.close_time[i]) / 1e9, UTC) + timedelta(seconds=5)
    await svc.on_evaluation("TESTUSDT", "15m", f, ev, True, close, {}, None, None)
    assert {t["variant_key"] for t in svc.open.values()} == {"A", "B"} and all(t["direction"] == direction for t in svc.open.values())
    long = direction == "LONG"
    for tid, t in list(svc.open.items()):
        assert (t["fill_price"] > px) if long else (t["fill_price"] < px)  # paid the spread either way
        assert (t["stop_loss"] < t["fill_price"] < t["take_profit"]) if long else (t["take_profit"] < t["fill_price"] < t["stop_loss"])
        if t["variant_key"] == "A":
            await svc._close(tid, "TARGET", t["take_profit"], close + timedelta(hours=1), "ticker_quote")
        else:
            await svc._close(tid, "STOP", t["stop_loss"], close + timedelta(hours=1), "ticker_quote")
    async with session_factory()() as session:
        trades = {t.variant_key: t for t in (await session.execute(select(PaperTrade))).scalars().all()}
        events = (await session.execute(select(NotificationEvent))).scalars().all()
    assert trades["A"].net_pnl > 0 and trades["A"].net_r > 0
    assert trades["B"].net_pnl < 0 and trades["B"].net_r < 0
    assert trades["C"].status == "REJECTED"
    titles = [e.title for e in events if e.paper_trade_id and e.event_type != "SIGNAL_REJECTED"]
    assert titles and all(direction in t and "PAPER TRADING" in t for t in titles)
    assert {e.event_type for e in events} >= {"PAPER_ENTRY_FILLED", "PAPER_STOP_LOSS_HIT"}
    await center.deliver_pending()
    assert tg.messages and all(direction in x for x in tg.messages)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
async def test_qualified_signal_alerts_in_both_directions(db, direction):
    from sqlalchemy import select

    from app.config import Settings
    from app.db.models import NotificationEvent
    from app.db.session import session_factory
    from app.notifications.service import NotificationService
    from app.signals.service import SignalService

    p, _, _, ev = _frame(direction)
    s = RuntimeSettings(strategy=p)
    tg = _Telegram()
    center = NotificationService(session_factory, _Hub(), _Store(s), tg)
    svc = SignalService(session_factory, _Hub(), Settings(_env_file=None), tg)
    svc.center, svc.settings = center, _Store(s)
    now = datetime.fromisoformat(ev.close_time) + timedelta(seconds=5)
    created = await svc.handle_setup("bybit", "TESTUSDT", ev, s, now, {"outcome": "QUALIFIED", "mode": "shadow"})
    assert created["direction"] == direction and created["notification_status"] == "SENT"
    assert len(tg.messages) == 1 and f"{direction} TESTUSDT" in tg.messages[0]
    assert ("🚀" if direction == "LONG" else "🔻") in tg.messages[0]
    async with session_factory()() as session:
        n = (await session.execute(select(NotificationEvent))).scalars().one()
    assert n.event_type == "SIGNAL_QUALIFIED" and n.title.startswith(direction) and n.payload["direction"] == direction
    long = direction == "LONG"
    pl = n.payload
    assert (pl["stop_loss"] < pl["entry_reference"] < pl["take_profit"]) if long else (pl["take_profit"] < pl["entry_reference"] < pl["stop_loss"])
    assert format_signal_message({**created, "candle_close_time": "2026-01-01 00:00"}).count(direction) >= 1


def test_notification_events_accept_both_directions_without_special_casing():
    for d in ("LONG", "SHORT"):
        assert EventIn(event_id=f"x{d}", event_type="SIGNAL_QUALIFIED", title=f"{d} X", payload={"direction": d}).payload["direction"] == d


# ----------------------------------------------------------------------------- backtest reporting

def test_backtest_metrics_separate_long_and_short():
    def t(pnl, r, d):
        return {"net_pnl": pnl, "r_multiple": r, "fees": 1, "funding": 0, "direction": d, "exit_reason": "TARGET", "bars_held": 3}

    m = compute_metrics([t(200, 2, "LONG"), t(-100, -1, "LONG"), t(-50, -0.5, "SHORT")], 10_000, [{"equity": 10_050}])
    assert m["long_trades"] == 2 and m["short_trades"] == 1
    long, short = m["by_direction"]["LONG"], m["by_direction"]["SHORT"]
    assert long["trades"] == 2 and long["net_pnl"] == 100 and long["win_rate_pct"] == 50
    assert long["avg_r_multiple"] == pytest.approx(0.5) and long["profit_factor"] == 2
    assert short["trades"] == 1 and short["net_pnl"] == -50 and short["win_rate_pct"] == 0 and short["profit_factor"] == 0
    assert compute_metrics([], 10_000, [])["by_direction"]["LONG"]["trades"] == 0


@pytest.mark.parametrize("drift,direction", [(0.0008, "LONG"), (-0.0008, "SHORT")])
def test_backtest_trades_in_both_directions(drift, direction):
    df = trending(drift)
    p = phase1_params(mtf={"enabled": False})
    cfg = BacktestConfig(symbol="TESTUSDT", timeframe="15m", start=df["open_time"].iloc[0].to_pydatetime(),
                         end=df["open_time"].iloc[-1].to_pydatetime(), strategy=p)
    res = run_backtest(cfg, df)
    assert res.trades and {t["direction"] for t in res.trades} == {direction}
    assert res.metrics["by_direction"][direction]["trades"] == len(res.trades)
