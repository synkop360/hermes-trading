"""Opt-in historical experiments. Never imported by the live execution path."""
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import replace
from decimal import Decimal as D, ROUND_DOWN

from hermes_trading.positions import PositionEngine, SimulationSettings, _round_quantity, _expected_payoff_covers_costs
from hermes_trading.execution_policy import initial_stop_price
from hermes_trading.research import aggregate_candles, snapshots


class ExperimentEngine(PositionEngine):
    def __init__(self, state, projector, policy):
        super().__init__(state, projector)
        self.policy = policy
        self.initial_risk = {}
        self.activated = set()

    def _entry(self, snapshot, decision, strategy, settings):
        result = super()._entry(snapshot, decision, strategy, settings)
        position = result[0]
        self.initial_risk[position.opened_at, position.asset] = position.entry_price-position.stop_price
        return result

    def _entry_notional(self, strategy, settings, fill_price, stop_price):
        if self.policy.current_notional is not None:
            return self.policy.current_notional
        return super()._entry_notional(strategy, settings, fill_price, stop_price)

    def _trail_stop(self, snapshot, strategy, position):
        if self.policy.options.get('delayed_trailing'):
            key = position.opened_at, position.asset
            if snapshot.candle.close-position.entry_price >= self.initial_risk[key]:
                self.activated.add(key)
            if key not in self.activated:
                return None
        return super()._trail_stop(snapshot, strategy, position)


class ExperimentPolicy:
    def __init__(self, options, data, timeframe):
        self.options = dict(options)
        self.counts = Counter()
        self.trends = {}
        self.approved = None
        self.current_notional = None
        if options.get('higher_trend'):
            for asset, candles in data.items():
                higher = aggregate_candles(candles, timeframe, '4h')
                points = []
                for snapshot in snapshots({asset: higher}, '4h'):
                    closes = snapshot.price['closes']
                    allowed = len(closes) >= 101 and sum(closes[-100:]) > sum(closes[-101:-1]) and closes[-1] > sum(closes[-100:])/100
                    points.append((snapshot.candle.close_timestamp, allowed))
                self.trends[asset] = ([t for t, _ in points], [v for _, v in points])

    def engine(self, state, projector):
        return ExperimentEngine(state, projector, self)

    def limit_price(self, prior, config):
        tick = D(config.get('research', {}).get('price_tick', '0.00000001'))
        return (prior.candle.close * D('.999')/tick).to_integral_value(rounding=ROUND_DOWN)*tick

    def prepare_batch(self, batch, pending, configs, engine, cash, equity):
        if not self.options.get('maker'):
            return
        self.approved = {}
        self.current_notional = None
        engine.equity = max(D(0), equity)
        active = engine.state.active_positions()
        exposure = sum((p.quantity*p.entry_price for p in active), D(0))
        slots = len(active)
        # Decide admission from information available at the open. Low/high/close
        # are deliberately not read until all resting-order reservations exist.
        for snapshot in batch:
            asset, candle = snapshot.asset, snapshot.candle
            if asset not in pending or engine.state.open_position(asset) is not None:
                continue
            prior, _ = pending[asset]
            if prior.candle.close_timestamp != candle.open_timestamp:
                continue
            config = configs[asset]
            if self.options.get('scalable_cap'):
                config['simulation']['max_order_quote'] = str(self.order_cap(engine.equity))
            settings = SimulationSettings.from_strategy(config)
            limit = self.limit_price(prior, config)
            self.counts['maker_submissions'] += 1
            if candle.open <= limit:
                self.counts['maker_crossing_rejected'] += 1
                continue
            if slots >= int(config['simulation']['max_concurrent_positions']):
                self.counts['maker_reserved_slot_rejected'] += 1
                continue
            if not _expected_payoff_covers_costs(config, settings, prior):
                self.counts['maker_cost_rejected'] += 1
                continue
            stop = initial_stop_price(prior, config, limit)
            notional = min(engine._entry_notional(config, settings, limit, stop),
                           max(D(0), equity*settings.max_total_exposure_pct-exposure),
                           max(D(0), cash)/(1+settings.maker_fee_rate))
            quantity = _round_quantity(notional/limit, settings)
            if quantity <= 0 or quantity < settings.minimum_order_quantity or quantity*limit < settings.minimum_order_quote:
                self.counts['maker_minimum_rejected'] += 1
                continue
            notional = quantity*limit
            self.approved[asset] = notional
            slots += 1
            exposure += notional
            cash -= notional*(1+settings.maker_fee_rate)

    def entry_price(self, prior, candle, config):
        if not self.options.get('maker'):
            return candle.open
        if self.approved is not None and prior.asset not in self.approved:
            return None
        self.current_notional = self.approved.get(prior.asset) if self.approved is not None else None
        limit = self.limit_price(prior, config)
        if candle.open <= limit:
            self.counts['maker_crossing_rejected'] += 1
            return None
        if candle.low > limit * D('.999'):
            self.counts['maker_expired'] += 1
            return None
        self.counts['maker_fill_opportunities'] += 1
        return limit

    def allow_entry(self, snapshot):
        if not self.options.get('higher_trend'):
            return True
        times, values = self.trends.get(snapshot.asset, ([], []))
        index = bisect_right(times, snapshot.candle.close_timestamp)-1
        allowed = index >= 0 and values[index]
        if not allowed:
            self.counts['higher_trend_rejected'] += 1
        return allowed

    def order_cap(self, equity):
        return min(D('25'), equity*D('.05'))

    def exit_signal(self, snapshot, position, config, signal, entered_this_bar):
        if self.options.get('fixed_target_only') and signal.reason == 'explicit_exit':
            return replace(signal, action='hold', reason='fixed_target_wait')
        if self.options.get('maker') and entered_this_bar and signal.reason in {'take_profit','take_profit_atr'}:
            self.counts['ambiguous_entry_bar_target_deferred'] += 1
            return replace(signal, action='hold', reason='entry_bar_target_deferred')
        if self.options.get('full_exit_fallback') and signal.reason == 'partial_exit':
            settings = SimulationSettings.from_strategy(config)
            leg = _round_quantity(position.quantity*D(config['exit']['partial_close_pct']), settings)
            smaller = min(leg, position.quantity-leg)
            if smaller < settings.minimum_order_quantity or smaller*snapshot.candle.close*(1-settings.slippage_rate) < settings.minimum_order_quote:
                self.counts['partial_converted_to_full'] += 1
                return replace(signal, reason='explicit_exit')
        return signal

    def summary(self, closed, active):
        open_keys = {(p.asset, p.opened_at) for p in active}
        grouped = defaultdict(lambda: D(0))
        for p in closed:
            if (p.asset, p.opened_at) not in open_keys:
                grouped[p.asset, p.opened_at] += p.realized_pnl
        wins = [p for p in grouped.values() if p > 0]
        losses = [p for p in grouped.values() if p < 0]
        return {'options': self.options, 'counts': dict(self.counts),
                'average_win': str(sum(wins, D(0))/len(wins)) if wins else None,
                'average_loss': str(sum(losses, D(0))/len(losses)) if losses else None,
                'completed_trade_pnl': [{'asset': a, 'opened_at': t, 'net_pnl': str(p)} for (a,t),p in sorted(grouped.items())]}
