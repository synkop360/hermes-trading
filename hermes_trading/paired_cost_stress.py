"""Conditional fixed-order cost sensitivity and original-entry trade units."""
from datetime import timedelta
from copy import deepcopy

from hermes_trading.reflection_supervision import _now, _time
from hermes_trading.wallet_metrics import COSTS, _decimal
from hermes_trading.operational_exits import RECOVERY_EXIT_REASONS


def configured_order_feasibility(order, strategy, snapshot, *, equity, spendable_cash,
                                 existing_exposure, open_positions, remaining_quantity=None,venue_required=False):
    """Conditional admission at 2x costs, preserving the original quantity/path.

    Equity is frozen before this batch's fills; cash and entry-basis exposure
    include earlier fills in this batch. Stops are recomputed from the same
    closed history at the stressed entry price, solely for the sizing guard.
    This does not rerun signals, stop triggers, exits or the LLM, and configured
    minimums do not establish current exchange requirements.
    """
    from dataclasses import replace
    from hermes_trading.positions import SimulationSettings, _round_quantity, _expected_payoff_covers_costs
    from hermes_trading.execution_policy import initial_stop_price
    from hermes_trading.strategy import _is_maker_entry
    settings=SimulationSettings.from_strategy(strategy)
    quantity,price,rate,slip=(_decimal(order.get(key)) for key in
                             ('quantity','fill_price','fee_rate','slippage_rate'))
    equity,cash,exposure=map(_decimal,(equity,spendable_cash,existing_exposure))
    side=order.get('side')
    if (side not in ('buy','sell') or quantity<=0 or price<=0 or exposure<0
        or type(open_positions) is not int or open_positions<0
        or rate!=settings.taker_fee_rate or slip!=settings.slippage_rate
        or order.get('asset')!=snapshot.asset or (side=='buy' and _is_maker_entry(strategy.get('entry')))
        or any(value>=_decimal('.5') for value in
               (settings.taker_fee_rate,settings.maker_fee_rate,settings.slippage_rate))):
        raise ValueError('invalid original order or doubled-cost settings')
    stressed_price=price/(1+slip)*(1+2*slip) if side=='buy' else price/(1-slip)*(1-2*slip)
    gross=quantity*stressed_price
    failures,unknowns=[],[]
    if venue_required or getattr(snapshot,'venue_rules',None) is not None:
        from hermes_trading.venue_constraints import parse_venue_constraints,VenueConstraintsError
        try:venue=parse_venue_constraints(snapshot.venue_rules,asset=snapshot.asset,at=_time(order['at']))
        except (VenueConstraintsError,KeyError):unknowns.append('current_venue_evidence_unavailable')
        else:
            try:
                venue.validate_order(quantity,stressed_price)
                if remaining_quantity is not None and _decimal(remaining_quantity)>0:
                    venue.validate_order(_decimal(remaining_quantity),stressed_price)
            except VenueConstraintsError:failures.append('current_venue_minimum_or_precision')
    def minimums(q, prefix=''):
        if q!=_round_quantity(q,settings):failures.append(prefix+'quantity_precision')
        if q<settings.minimum_order_quantity:failures.append(prefix+'minimum_order_quantity')
        if q*stressed_price<settings.minimum_order_quote:failures.append(prefix+'minimum_order_quote')
    minimums(quantity)
    result={'quantity':str(quantity),'stressed_fill_price':str(stressed_price),
            'stressed_notional':str(gross),'equity_basis':'frozen_prebatch_net_liquidation',
            'frozen_stressed_equity':str(equity),'spendable_cash_before_order':str(cash),
            'entry_basis_exposure_before_order':str(exposure),'open_positions_before_order':open_positions,
            'cash_basis':'standalone_capital_less_accrued_costs',
            'scope':'configured_admission_not_exchange_verification_or_strategy_replay'}
    if side=='buy':
        rule=strategy.get('exit',{})
        uses_history=any(key in rule for key in ('stop_atr','take_profit_atr','trailing_atr'))
        history_ok=True
        if uses_history:
            from hermes_trading.market_data import _closed_candle,timeframe_interval
            try:
                raw=(snapshot.price or {}).get('candles')
                if not isinstance(raw,(list,tuple)) or len(raw)<15 or snapshot.candle is None:
                    raise ValueError('missing closed history')
                candles=[_closed_candle(row,timeframe_interval(snapshot.timeframe)) for row in raw]
                times=[row.close_timestamp for row in candles]
                if (any(right<=left for left,right in zip(times,times[1:]))
                    or times[-1]!=snapshot.candle.close_timestamp
                    or any(getattr(candles[-1],key)!=getattr(snapshot.candle,key) for key in ('open','high','low','close'))):
                    raise ValueError('closed history differs')
            except (ValueError,TypeError,KeyError,AttributeError,ArithmeticError):
                history_ok=False;unknowns.append('closed_history_provenance_unavailable')
        if equity<=0:failures.append('nonpositive_equity')
        if gross>settings.max_order_quote:failures.append('max_order_quote')
        if gross>equity*settings.max_symbol_exposure_pct:failures.append('symbol_exposure')
        if gross+exposure>equity*settings.max_total_exposure_pct:failures.append('portfolio_exposure')
        if gross*(1+2*rate)>cash:failures.append('cash')
        cap=strategy.get('simulation',{}).get('max_concurrent_positions')
        if cap is not None:
            cap=_decimal(cap)
            if cap<=0 or cap!=int(cap):raise ValueError('invalid concurrency cap')
            if open_positions>=cap:failures.append('concurrent_positions')
        r=_decimal(strategy.get('position_size_r','.5'))
        if r<=0:raise ValueError('positive position_size_r required')
        risk_budget=equity*settings.risk_per_trade_pct*r
        result['risk_budget']=str(risk_budget)
        if order.get('initial_stop_price') is None:
            unknowns.append('original_initial_stop_missing')
        else:
            try:
                if 'stop_atr' in rule and not history_ok:raise ValueError('unknown closed stop history')
                original_stop=initial_stop_price(snapshot,strategy,price)
                stressed_stop=initial_stop_price(snapshot,strategy,stressed_price)
            except ValueError:
                unknowns.append('initial_stop_geometry_unavailable')
            else:
                if _decimal(order['initial_stop_price'])!=original_stop:
                    raise ValueError('original initial stop differs from retained contract and closed history')
                stop_fraction=(stressed_price-stressed_stop)/stressed_price
                risk=gross*(stop_fraction+2*rate+2*settings.taker_fee_rate+4*slip)
                result.update(stressed_initial_stop=str(stressed_stop),risk_amount=str(risk))
                if risk>risk_budget:failures.append('risk_budget')
        doubled=replace(settings,taker_fee_rate=2*settings.taker_fee_rate,
                        maker_fee_rate=2*settings.maker_fee_rate,slippage_rate=2*settings.slippage_rate)
        if not history_ok and any(key in rule for key in ('take_profit_atr','trailing_atr')):
            unknowns.append('configured_payoff_history_unavailable')
        elif not _expected_payoff_covers_costs(strategy,doubled,snapshot):
            failures.append('configured_payoff_cost_filter')
    elif remaining_quantity is not None:
        remainder=_decimal(remaining_quantity)
        if remainder<0:raise ValueError('negative original inventory remainder')
        if remainder>0:minimums(remainder,'remaining_')
    result.update(failures=failures,unknowns=unknowns,
                  feasible=False if failures else (None if unknowns else True))
    return result


class FixedOrderCostStress:
    def __init__(self, starting_balance):
        self.initial = _decimal(starting_balance)
        if self.initial <= 0:
            raise ValueError('positive starting capital required')
        self.base_cash = self.stressed_cash = self.initial
        self.holdings = {}
        self.last_at = None
        self.cash_feasible = True
        self.cash_failures = []
        self.insolvent = False
        self.known_operating_costs = _decimal(0)
        self.configured_orders_checked = 0
        self.configured_policy_failures = []
        self.configured_policy_unknowns = []

    def _reserve_costs(self, at, asset=None):
        # Conservative accrual reservation, not an assumed exchange withdrawal.
        spendable = self.stressed_cash-2*self.known_operating_costs
        if spendable < 0:
            self.cash_feasible = False
            self.cash_failures.append({'at':at.isoformat(),'asset':asset,
                'cash':str(self.stressed_cash),'spendable_cash':str(spendable),
                'reserved_operating_costs':str(2*self.known_operating_costs),
                'basis':'standalone_capital_less_accrued_costs'})
        return spendable

    def apply_order(self, order):
        at = _time(order.get('at'))
        if self.last_at is not None and at < self.last_at:
            raise ValueError('original order sequence is not causal')
        asset, side = order.get('asset'), order.get('side')
        if not isinstance(asset,str) or asset.count('/') != 1 or not asset.endswith('/EUR') or side not in {'buy','sell'}:
            raise ValueError('original long EUR spot order required')
        quantity, price = _decimal(order.get('quantity')), _decimal(order.get('fill_price'))
        fee, rate, slip = (_decimal(order.get(key)) for key in ('fee','fee_rate','slippage_rate'))
        if quantity <= 0 or price <= 0 or not 0 <= rate < _decimal('.5') or not 0 <= slip < _decimal('.5'):
            raise ValueError('invalid original fill or doubled-cost range')
        if fee != quantity*price*rate:
            raise ValueError('original fill fee does not reconcile with its entry contract')
        holdings = deepcopy(self.holdings)
        if side == 'buy':
            if asset in holdings:
                raise ValueError('original order stacks existing inventory')
            stressed_price = price/(_decimal(1)+slip)*(_decimal(1)+2*slip)
            base_cash = self.base_cash-quantity*price-fee
            cash = self.stressed_cash-quantity*stressed_price*(1+2*rate)
            holdings[asset] = {'quantity':quantity,'base_entry':price,'stress_entry':stressed_price,
                               'entry_fee_rate':rate,'slippage_rate':slip,
                               'strategy_version':str(order.get('strategy_version',''))}
        else:
            holding = holdings.get(asset)
            if holding is None or quantity > holding['quantity']:
                raise ValueError('original sell exceeds inventory')
            if slip != holding['slippage_rate']:
                raise ValueError('original exit differs from retained entry slippage')
            stressed_price = price/(1-slip)*(1-2*slip)
            base_cash = self.base_cash+quantity*price-fee
            cash = self.stressed_cash+quantity*stressed_price*(1-2*rate)
            holding['quantity'] -= quantity
            if holding['quantity'] == 0:
                del holdings[asset]
        if base_cash < 0:
            raise ValueError('original orders exceed original cash')
        self.base_cash,self.stressed_cash,self.holdings,self.last_at = base_cash,cash,holdings,at
        self._reserve_costs(at, asset)

    def observe(self, observation):
        at = _time(observation.get('at'))
        protocol=observation.get('execution_venue_protocol')
        if protocol is not None and (protocol!='dated_rules_v1' or observation.get('liquidation_complete') is not True):
            raise ValueError('venue liquidation completeness is unavailable')
        if self.last_at is not None and at < self.last_at:
            raise ValueError('observation precedes original orders')
        if _decimal(observation.get('trading_cash')) != self.base_cash:
            raise ValueError('original observation cash does not reconcile with original orders')
        marks = observation.get('mark_provenance',{})
        if set(marks) != set(self.holdings):
            raise ValueError('original inventory marks are incomplete')
        original_liquidation = stressed_liquidation = basis = entry_fees = _decimal(0)
        for asset,holding in self.holdings.items():
            mark = marks[asset]
            bid,rate,slip = (_decimal(mark.get(key)) for key in ('bid','exit_fee_rate','slippage_rate'))
            age = at-_time(mark.get('observed_at'))
            if bid <= 0 or not 0 <= rate < _decimal('.5') or not 0 <= slip < _decimal('.5'):
                raise ValueError('invalid retained liquidation costs')
            if not timedelta(0) <= age <= timedelta(minutes=5) or slip != holding['slippage_rate']:
                raise ValueError('original liquidation mark is stale or contract differs')
            quantity = holding['quantity']
            if protocol=='dated_rules_v1':
                from hermes_trading.venue_constraints import parse_venue_constraints
                venue=parse_venue_constraints(mark.get('venue_rules'),asset=asset,at=at)
                venue.validate_order(quantity,bid*(1-slip))
                venue.validate_order(quantity,bid*(1-2*slip))
            original_liquidation += quantity*bid*(1-slip)*(1-rate)
            stressed_liquidation += quantity*bid*(1-2*slip)*(1-2*rate)
            basis += quantity*holding['stress_entry']
            entry_fees += quantity*holding['stress_entry']*2*holding['entry_fee_rate']
        if original_liquidation != _decimal(observation.get('open_liquidation_value')):
            raise ValueError('original liquidation value does not reconcile')
        if observation.get('net_equity') is None or observation.get('missing'):
            raise ValueError('unknown original net equity')
        costs = observation.get('cost_provenance',{})
        if set(costs) != COSTS:
            raise ValueError('complete original costs required')
        total = sum((_decimal(event.get('amount')) for event in costs.values()), _decimal(0))
        if total != _decimal(observation.get('known_accrued_costs')):
            raise ValueError('original costs do not reconcile')
        if _decimal(observation['net_equity']) != self.base_cash+original_liquidation-total:
            raise ValueError('original net equity does not reconcile')
        net = self.stressed_cash+stressed_liquidation-2*total
        self.known_operating_costs = total
        spendable = self._reserve_costs(at)
        self.last_at = at
        self.insolvent = self.insolvent or net <= 0
        result = deepcopy(observation)
        for event in result['cost_provenance'].values():
            event['amount'] = str(2*_decimal(event['amount']))
        result.update(trading_cash=str(self.stressed_cash),open_liquidation_value=str(stressed_liquidation),
                      known_accrued_costs=str(2*total),known_cost_equity=str(net),net_equity=str(net),
                      open_basis=str(basis),open_entry_fees=str(entry_fees),
                      closed_net_pnl=str(self.stressed_cash-self.initial+basis+entry_fees),
                      stress_model='fixed_original_orders_2x_fees_slippage_operating_costs',
                      stressed_cash_feasible=self.cash_feasible,
                      stressed_spendable_cash=str(spendable),
                      stressed_cash_basis='standalone_capital_less_accrued_costs',
                      evidence='conditional_cost_sensitivity_not_strategy_reoptimization')
        return result


def completed_entry_lineages(positions, entries, *, start, end):
    start,end = _now(start),_now(end)
    if not start < end <= start+timedelta(days=180):
        raise ValueError('bounded lineage window required')
    def signature(row):
        return (row['asset'],_time(row['opened_at']),_decimal(row['entry_price']),str(row['strategy_version']))
    originals,identities = {},set()
    for entry in entries:
        key = signature(entry)
        if entry['position_id'] in identities or key in originals:
            raise ValueError('ambiguous original entry lineage')
        identities.add(entry['position_id'])
        originals[key] = entry
    groups = {entry['position_id']:[] for entry in entries}
    seen = set()
    for row in positions:
        identity = row['position_id']
        if identity in seen:
            raise ValueError('duplicate position identity')
        seen.add(identity)
        if _time(row['opened_at']) > end:
            continue
        entry = originals.get(signature(row))
        if entry is None:
            raise ValueError('position has no unique original entry')
        groups[entry['position_id']].append(row)
    counts,excluded,completed = [0,0],[],[]
    for entry in entries:
        root = next((row for row in groups[entry['position_id']] if row['position_id']==entry['position_id']),None)
        if root is None:
            raise ValueError('original entry position is missing')
        if root['status'] != 'closed' or _time(root['closed_at']) > end:
            continue
        closed = _time(root['closed_at'])
        if not start < closed <= end or _time(root['opened_at']) < start:
            continue
        total = _decimal(0)
        recovery = False
        for leg in groups[entry['position_id']]:
            if leg['status'] != 'closed' or not _time(leg['opened_at']) <= _time(leg['closed_at']) <= closed:
                raise ValueError('closed original lineage contains unclosed or acausal legs')
            quantity = _decimal(leg['quantity'])
            if quantity <= 0:
                raise ValueError('invalid original lineage quantity')
            total += quantity
            reason = leg.get('exit_reason')
            if not isinstance(reason,str) or not reason:
                raise ValueError('missing original exit provenance')
            recovery = recovery or 'recovery' in reason or reason in RECOVERY_EXIT_REASONS
        if total != _decimal(entry['quantity']):
            raise ValueError('original entry quantity does not reconcile across partial legs')
        if recovery:
            excluded.append(entry['position_id'])
            continue
        counts[0 if closed <= start+timedelta(days=90) else 1] += 1
        completed.append(entry['position_id'])
    return {'completed':len(completed),'completed_by_90day_block':counts,
            'entry_ids':completed,'excluded_recovery_lineages':excluded,
            'unit':'fully_closed_original_entry_not_partial_legs'}
