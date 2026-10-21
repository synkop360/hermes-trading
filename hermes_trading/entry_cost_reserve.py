"""Opt-in sizing admission, never doubled charges or changed historical fills."""
from decimal import Decimal
from pathlib import Path

from hermes_trading.wallet_metrics import _decimal


def reserve_enabled(strategy):
    value=strategy.get('simulation',{}).get('cost_stress_reserve',False)
    if type(value) is not bool:raise ValueError('cost_stress_reserve must be a literal boolean')
    return value


def current_stress_book(state_dir, starting_balance, at):
    from hermes_trading.paired_performance_report import _canonical_account
    from hermes_trading.paired_cost_stress import FixedOrderCostStress
    book=FixedOrderCostStress(starting_balance)
    orders,_,_= _canonical_account(Path(state_dir),at)
    for order in orders:book.apply_order(order)
    return book


def frozen_reserve_context(state_dir, observation):
    from hermes_trading.reflection_supervision import _time
    at=_time(observation['at'])
    book=current_stress_book(state_dir,observation['starting_balance'],at)
    stressed=book.observe(observation)
    return {'status':'ready','at':observation['at'],'starting_balance':observation['starting_balance'],
        'equity':stressed['net_equity'],'operating_costs':observation['known_accrued_costs'],
        'cash':stressed['stressed_spendable_cash'],
        'scope':'frozen_prebatch_fixed_original_fills_2x_cost_reserve'}


def reserved_quantity(strategy,snapshot,fill_price,ordinary_quantity,*,equity,cash,exposure,positions,at=None):
    from hermes_trading.positions import SimulationSettings,_round_quantity
    from hermes_trading.execution_policy import initial_stop_price
    from hermes_trading.paired_cost_stress import configured_order_feasibility
    quantity,price,equity,cash,exposure=map(_decimal,(ordinary_quantity,fill_price,equity,cash,exposure))
    if quantity<=0 or equity<=0 or cash<=0:return Decimal(0)
    settings=SimulationSettings.from_strategy(strategy)
    if getattr(snapshot,'venue_rules',None) is not None:
        from dataclasses import replace
        from hermes_trading.venue_constraints import parse_venue_constraints,VenueConstraintsError
        if at is None:return Decimal(0)
        try:venue=parse_venue_constraints(snapshot.venue_rules,asset=snapshot.asset,at=at)
        except VenueConstraintsError:return Decimal(0)
        settings=replace(settings,quantity_decimals=min(venue.quantity_decimals,
            settings.quantity_decimals if settings.quantity_decimals is not None else venue.quantity_decimals))
    try:stop=initial_stop_price(snapshot,strategy,price)
    except ValueError:return Decimal(0)
    order={'asset':snapshot.asset,'side':'buy','quantity':str(quantity),'fill_price':str(price),
        'fee_rate':str(settings.taker_fee_rate),'slippage_rate':str(settings.slippage_rate),
        'initial_stop_price':str(stop)}
    if at is not None:
        from hermes_trading.reflection_supervision import _time
        order['at']=_time(at).isoformat() if isinstance(at,str) else at.isoformat()
    def check(q):
        return configured_order_feasibility(dict(order,quantity=str(q)),strategy,snapshot,
            equity=equity,spendable_cash=cash,existing_exposure=exposure,open_positions=positions)
    probe=check(quantity)
    if probe['unknowns'] or 'risk_amount' not in probe or _decimal(probe['risk_amount'])<=0:return Decimal(0)
    stressed_price=_decimal(probe['stressed_fill_price'])
    bound=min(quantity,_decimal(probe['risk_budget'])/(_decimal(probe['risk_amount'])/quantity),
        settings.max_order_quote/stressed_price,equity*settings.max_symbol_exposure_pct/stressed_price,
        max(Decimal(0),equity*settings.max_total_exposure_pct-exposure)/stressed_price,
        cash/(stressed_price*(1+2*settings.taker_fee_rate)))
    q=_round_quantity(bound,settings)
    if q<=0:return Decimal(0)
    return q if check(q)['feasible'] is True else Decimal(0)


def reserved_notional(engine,strategy,settings,snapshot,fill_price,ordinary_notional):
    from hermes_trading.reflection_supervision import _time
    context=engine.entry_cost_reserve
    if (engine.prospective_at is None or engine.reserve_state_dir is None or snapshot is None
            or not isinstance(context,dict) or context.get('status')!='ready'
            or _time(context['at'])!=engine.prospective_at):return Decimal(0)
    book=current_stress_book(engine.reserve_state_dir,context['starting_balance'],engine.prospective_at)
    costs=_decimal(context['operating_costs'])
    exposure=sum((row['quantity']*row['stress_entry'] for row in book.holdings.values()),Decimal(0))
    if snapshot.asset in book.holdings:return Decimal(0)
    q=reserved_quantity(strategy,snapshot,fill_price,ordinary_notional/fill_price,
        equity=context['equity'],cash=book.stressed_cash-2*costs,exposure=exposure,positions=len(book.holdings),at=engine.prospective_at)
    return q*fill_price
