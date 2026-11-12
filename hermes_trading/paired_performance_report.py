"""Read-only primary paired report. No legacy/live qualification promotion."""
import argparse
from contextlib import ExitStack,closing
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from decimal import DecimalException
import yaml

from hermes_trading.entry_contracts import EntryContractResolver
from hermes_trading.loop import _load_yaml
from hermes_trading.paired_packet_storage import load_packet
from hermes_trading.paired_statistics import (aligned_calendar_equity, paired_circular_bootstrap,
                                             chronological_growth_blocks, BOOTSTRAP_SEED, BOOTSTRAP_RESAMPLES)
from hermes_trading.paired_cost_stress import FixedOrderCostStress, completed_entry_lineages
from hermes_trading.positions import SimulationSettings
from hermes_trading.reflection_supervision import _hash, _now, _time
from hermes_trading.paired_paper import _decode
from hermes_trading.wallet_metrics import COSTS, _decimal
from hermes_trading.paired_primary import resolve_primary,reporting_protocol_for

REPORTING_PROTOCOL = {
    'schema_version':1,'primary_wallet_eur':100,'assessment_days':180,'block_window_days':90,
    'minimum_completed_entry_lineages_per_block':30,'bootstrap_seed':BOOTSTRAP_SEED,
    'bootstrap_resamples':BOOTSTRAP_RESAMPLES,'generator':'PCG64','primary_block_days':7,
    'sensitivity_block_days':14,'confidence':'one_sided_95_percentile_lower_5pct_linear',
    'calendar_boundary_tolerance_seconds':120,'maximum_observation_gap_seconds':300,
    'cost_stress_multiplier':2,'stress':'fixed_original_orders_and_entry_contracts',
    'configured_stress_admission':'frozen_prebatch_net_equity_sequential_cash_and_entry_basis_exposure',
    'stressed_stop_geometry':'same_verified_closed_history_at_stressed_entry_for_sizing_only',
    'secondary_wallets':'exploratory_not_independent_replications',
}


def _readonly(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    return db


def _canonical_account(path,end,*,database=None):
    """Original fills, quantities, archived contracts and exit provenance."""
    resolver=EntryContractResolver(path,_load_yaml(path/'strategy.yaml'))
    orders,entries,positions=[],[],[]
    db=database if database is not None else _readonly(path/'trading.db')
    try:
        if db.execute('SELECT 1 FROM orders o LEFT JOIN order_intents i ON i.client_order_id=o.client_order_id '
                      'LEFT JOIN positions p ON p.position_id=i.position_id '
                      'WHERE o.created_at<=? AND (i.client_order_id IS NULL OR p.position_id IS NULL) LIMIT 1',
                      (end.isoformat(),)).fetchone():
            raise ValueError('original order has no immutable position linkage')
        for row in db.execute('SELECT * FROM positions ORDER BY opened_at,position_id'):
            item=dict(row)
            if _time(item['opened_at'])>end:continue
            if item['status']=='closed' and _time(item['closed_at'])<=end:
                exits=list(db.execute(
                    "SELECT d.reason FROM order_intents i JOIN decisions d ON d.decision_id=i.decision_id "
                    "WHERE i.position_id=? AND i.side='sell' AND d.decided_at=?",
                    (item['position_id'],item['closed_at'])))
                if len(exits)!=1:raise ValueError('closed position lacks unique immutable exit provenance')
                item['exit_reason']=exits[0][0]
            positions.append(item)
        for row in db.execute(
            'SELECT o.*,o.rowid AS original_sequence,p.strategy_version,p.opened_at,p.entry_price,i.position_id,i.stop_price AS initial_stop_price '
            'FROM orders o JOIN order_intents i ON i.client_order_id=o.client_order_id '
            'JOIN positions p ON p.position_id=i.position_id ORDER BY o.created_at,o.rowid'):
            if _time(row['created_at'])>end:continue
            if row['status']!='filled' or row['filled_quantity']!=row['requested_quantity']:
                raise ValueError('original paper order is not fully filled')
            settings=SimulationSettings.from_strategy(resolver(row['asset'],row['strategy_version']))
            # Passive fill evidence is outside the current taker-only protocol.
            if row['order_type']!='market':raise ValueError('passive fill quality is not qualified')
            orders.append({'at':row['created_at'],'asset':row['asset'],'side':row['side'],
                           'quantity':row['filled_quantity'],'fill_price':row['average_fill_price'],
                           'fee':row['fees'],'fee_rate':str(settings.taker_fee_rate),
                           'slippage_rate':str(settings.slippage_rate),'original_sequence':row['original_sequence'],
                           'strategy_version':str(row['strategy_version']),
                           'initial_stop_price':row['initial_stop_price']})
            if row['side']=='buy':
                entries.append({'position_id':row['position_id'],'asset':row['asset'],
                                'opened_at':row['opened_at'],'entry_price':row['entry_price'],
                                'strategy_version':row['strategy_version'],'quantity':row['filled_quantity']})
    finally:
        if database is None:db.close()
    return orders,positions,entries


def _settled_pairs(db,configs,start,end,stress,orders,*,opening=None,source_validator=None,primary=None):
    indices=[0,0]
    primary=resolve_primary({}) if primary is None else primary
    names=tuple(primary.profile_key(arm) for arm in ('adaptive','control'))
    if opening is not None:
        pair=[]
        for index,name in enumerate(names):
            observation=opening['accounts'][name]
            pair.append((observation,stress[index].observe(observation)))
        yield pair

    for row in db.execute(
        "SELECT b.batch_id,b.input_hash,b.completed,b.payload AS original_packet,s.payload FROM paired_batches b "
        "LEFT JOIN paired_settlements s ON s.batch_id=b.batch_id WHERE b.status='complete' ORDER BY b.rowid"):
        packet=load_packet(db,row['original_packet'])
        if _hash(packet)!=row['input_hash']:raise ValueError('original packet hash differs')
        packet_at=_time(packet['created_at'])
        if packet_at>end or packet_at<start-timedelta(minutes=2):continue
        if opening is not None and packet_at<=start:
            raise ValueError('settled batch does not follow authoritative inception')
        if row['payload'] is None:raise ValueError('complete batch is missing its settlement')
        if source_validator is not None:source_validator.validate_packet(packet)
        final=load_packet(db,row['completed']);settlement=load_packet(db,row['payload'])
        pair=[]
        for index,name in enumerate(names):
            measurement=settlement[name];observation=measurement['observation']
            if observation is None or measurement['measurement_error'] is not None:
                raise ValueError('unknown settled primary measurement')
            at=_time(observation['at'])
            if at!=packet_at:raise ValueError('settled observation differs from durable packet time')
            if observation.get('experiment_id')!=configs[index]['experiment_id'] or observation.get('account_config_hash')!=_hash(configs[index]):
                raise ValueError('settled observation account binding differs')
            if (final[name].get('finalized') is not True or final[name].get('input_hash')!=row['input_hash']
                or final[name].get('net_equity')!=measurement['net_equity']
                or observation['net_equity']!=measurement['net_equity']):
                raise ValueError('shared settlement differs from completed batch')
            if at<start-timedelta(minutes=2):continue
            budget=_decode(packet['budgets'][name])
            costs=budget.get('cost_provenance',{})
            if set(costs)!=COSTS or _hash(costs)!=_hash(observation['cost_provenance']):
                raise ValueError('settled costs differ from frozen pre-batch costs')
            total=sum((_decimal(event['amount']) for event in costs.values()),_decimal(0))
            if total!=_decimal(budget['known_accrued_costs']):
                raise ValueError('frozen pre-batch costs do not reconcile')
            # Reserve this batch's already accrued costs before any original fill.
            stress[index].known_operating_costs=total
            stress[index]._reserve_costs(at)
            frozen_stress_equity=(source_validator.stressed_prebatch_equity(packet,name,stress[index])
                                  if source_validator is not None else None)
            while indices[index]<len(orders[index]) and _time(orders[index][indices[index]]['at'])<=at:
                if source_validator is not None:
                    source_validator.validate_fill(packet,name,orders[index][indices[index]],holdings=stress[index].holdings)
                    guard=source_validator.validate_configured_stress(packet,name,orders[index][indices[index]],
                                                                      stress[index],frozen_stress_equity)
                    stress[index].configured_orders_checked+=1
                    receipt={'at':at.isoformat(),'asset':orders[index][indices[index]]['asset'],
                             'side':orders[index][indices[index]]['side'],**guard}
                    if guard['failures']:stress[index].configured_policy_failures.append(receipt)
                    if guard['unknowns']:stress[index].configured_policy_unknowns.append(receipt)
                stress[index].apply_order(orders[index][indices[index]])
                indices[index]+=1
            if source_validator is not None:
                source_validator.validate_observation(packet,name,observation,stress[index].holdings)
            pair.append((observation,stress[index].observe(observation)))
        if not pair:continue
        if len(pair)!=2:raise ValueError('primary shared observations do not align')
        yield pair

    if any(index!=len(account_orders) for index,account_orders in zip(indices,orders)):
        raise ValueError('unconsumed canonical fills lack complete common settlements')


def paired_performance_report(root, *, now=None, history=None):
    """Report intact account evidence, optionally with pinned shared snapshots.

    Explicit history is disabled unless enabled=True and requires sources,
    cache_root, max_bytes and timeout_seconds. Its last source must be this
    selected primary journal in a consistent offline snapshot. Caller-verified
    pins are not a recovery permission. The explicit recorded_archive_store_v1
    format resolves ancestors from a caller-pinned current journal using
    current,archive_root,cache_root,max_bytes,timeout_seconds and enabled.
    """
    root,current=Path(root),_now(now)
    result={'schema_version':1,'primary_wallet_eur':100,'observed_at':current.isoformat(),
            'profitability_qualified':False,'eligible_for_real_trading':False,
            'reporting_protocol':REPORTING_PROTOCOL,'blockers':[],
            'kind':'unactivated_or_incomplete','secondary_wallets':'exploratory_only'}
    evaluating_scenario=None
    try:
        raw=(root/'manifest.json').read_bytes();manifest=json.loads(raw)
        primary=resolve_primary(manifest)
        protocol=reporting_protocol_for(manifest,REPORTING_PROTOCOL)
        result.update(schema_version=2 if primary.versioned else 1,primary_wallet_eur=primary.wallet_eur,
                      reporting_protocol=protocol)
        result['manifest_sha256']=hashlib.sha256(raw).hexdigest()
        if manifest.get('status')!='paper_ready':
            result['blockers'].append('experiment_not_activated');return result
        from hermes_trading.paired_paper import _profiles
        profiles=_profiles(root,manifest)
        from hermes_trading.archive_quote_state import assert_quote_cohort
        assert_quote_cohort(root,manifest=manifest,sealed=history is not None)
        paths=[profiles[(arm,primary.wallet_eur)][0] for arm in ('adaptive','control')]
        configs=[profiles[(arm,primary.wallet_eur)][2] for arm in ('adaptive','control')]
        start=_time(configs[0]['activated_at'])
        if _time(configs[1]['activated_at'])!=start or current<start:
            raise ValueError('paired inception differs or is future-dated')
        days=min(180,(current-start).days)
        if days<180:result['blockers'].append('assessment_before_day180')
        from hermes_trading.paired_inception import read_paired_inception
        opening=read_paired_inception(root)
        start=_time(opening['at'])
        if start>current:raise ValueError('opening receipt is future-dated')
        days=min(180,(current-start).days)
        if days<180:result['blockers'].append('assessment_before_day180')
        result['inception_at']=start.isoformat()
        result['kind']='final_assessment' if days==180 else 'operational_learning_checkpoint'
        if manifest.get('reporting_protocol')!=protocol:
            result['blockers'].append('reporting_protocol_not_frozen_in_manifest')
        # An implemented kernel does not establish coverage for a changing strategy.
        result['blockers'].append('statistical_assumptions_and_coverage_not_independently_validated')
        result['blockers'].append('stressed_venue_and_risk_policy_feasibility_not_fully_verified')
        if days<1:return result
        end=start+timedelta(days=days)
        canonical=[_canonical_account(path,end) for path in paths]
        from hermes_trading.paired_source_validation import PrimarySourceValidator
        source_validator=PrimarySourceValidator(manifest,profiles)
        result['lineages']={arm:completed_entry_lineages(material[1],material[2],start=start,end=end)
                            for arm,material in zip(('adaptive','control'),canonical)}
        for arm,lineages in result['lineages'].items():
            if lineages['excluded_recovery_lineages']:result['blockers'].append(arm+'_recovery_outcomes')
            if days==180 and any(count<30 for count in lineages['completed_by_90day_block']):
                result['blockers'].append(arm+'_insufficient_completed_entry_lineages')
        shared_path=paths[0]/'paper_publications.sqlite'
        with ExitStack() as history_scope:
            if history is None:
                db=history_scope.enter_context(closing(_readonly(shared_path)))
                from hermes_trading.archive_journal import _checkpoint
                checkpoint=_checkpoint(db)
                if checkpoint is not None and checkpoint['retired_complete']:
                    raise ValueError('retired shared history is unavailable')
                count,first,last=db.execute('SELECT COUNT(*),MIN(rowid),MAX(rowid) FROM paired_batches').fetchone()
                if count and (first!=1 or last!=count):
                    raise ValueError('original shared history sequence is incomplete')
                history_db=db
            else:
                if not isinstance(history,dict):
                    raise ValueError('shared report history configuration differs')
                if 'format' in history:
                    from hermes_trading.archive_shared_resolver import recorded_shared_history,FORMAT
                    if history['format']!=FORMAT or set(history)!={'enabled','format','current','archive_root','cache_root','max_bytes','timeout_seconds'}:
                        raise ValueError('recorded shared history format/configuration differs')
                    if Path(history['current']['path']).resolve()!=shared_path.resolve():
                        raise ValueError('recorded history current source differs from selected journal')
                    view=history_scope.enter_context(recorded_shared_history(**history,
                        shared_relative_path=primary.profile_directory(manifest,'adaptive')+'/paper_publications.sqlite',
                        expected_cohort_sha256=_hash(manifest),expected_experiment_id=configs[0]['experiment_id'],now=current))
                else:
                    from hermes_trading.archive_shared_history import shared_history
                    if set(history)!={'enabled','sources','cache_root','max_bytes','timeout_seconds'}:
                        raise ValueError('shared report history configuration differs')
                    if Path(history['sources'][-1]['path']).resolve()!=shared_path.resolve():
                        raise ValueError('shared report history current source differs from selected journal')
                    view=history_scope.enter_context(shared_history(**history,
                        expected_cohort_sha256=_hash(manifest),expected_experiment_id=configs[0]['experiment_id']))
                db,history_db=view.current,view.history
            identity=db.execute('SELECT manifest_hash FROM paired_identity WHERE id=1').fetchone()
            if identity is None or identity[0]!=_hash(manifest):raise ValueError('shared manifest differs')
            if primary.versioned:
                from hermes_trading.paired_risk import validate_risk_state
                risk=db.execute('SELECT state FROM paired_risk_state WHERE id=1').fetchone()
                if risk is None:raise ValueError('primary risk state is missing')
                validate_risk_state(json.loads(risk[0]),primary)
            if db.execute("SELECT 1 FROM paired_batches WHERE status='pending' LIMIT 1").fetchone():
                raise ValueError('shared batch is pending')
            source_validator.prepare_publications(db,now=current)
            if source_validator.publication_error_class is not None:
                result['publication_source_error_class']=source_validator.publication_error_class
            # Two passes keep full intraday observations out of memory.
            summaries={}
            result['calendar']=summaries
            for scenario in ('base','stress'):
                evaluating_scenario=scenario
                engines=[FixedOrderCostStress(str(primary.wallet_eur)),FixedOrderCostStress(str(primary.wallet_eur))]
                pairs=_settled_pairs(history_db,configs,start,end,engines,[item[0] for item in canonical],opening=opening,
                                    source_validator=source_validator,primary=primary)
                selected=((pair[0][0],pair[1][0]) if scenario=='base' else (pair[0][1],pair[1][1]) for pair in pairs)
                try:
                    summaries[scenario]=aligned_calendar_equity(selected,start=start,days=days,starting_balance=str(primary.wallet_eur))
                finally:
                    result.setdefault('stress_feasibility',{})[scenario]={
                        'cash_basis':'standalone_capital_less_accrued_costs',
                        'cash_failures':[engine.cash_failures for engine in engines],
                        'insolvent':[engine.insolvent for engine in engines],
                        'configured_orders_checked':[engine.configured_orders_checked for engine in engines],
                        'configured_policy_failures':[engine.configured_policy_failures for engine in engines],
                        'configured_policy_unknowns':[engine.configured_policy_unknowns for engine in engines],
                        'configured_policy_assessment_complete':scenario in summaries,
                        'configured_policy_feasible':[False if engine.configured_policy_failures else
                            (None if engine.configured_policy_unknowns or scenario not in summaries else True) for engine in engines],
                        'scope':'configured_admission_not_exchange_verification_or_strategy_replay'}
                    if any(not engine.cash_feasible for engine in engines):
                        result['blockers'].append('stressed_original_orders_cash_infeasible')
                    if any(engine.insolvent for engine in engines):result['blockers'].append('stress_insolvency')
                    if any(engine.configured_policy_failures for engine in engines):
                        result['blockers'].append('stressed_original_orders_configured_policy_infeasible')
                    if any(engine.configured_policy_unknowns for engine in engines):
                        result['blockers'].append('stressed_original_orders_configured_policy_evidence_missing')
        for scenario,calendar in result['calendar'].items():
            calendar['chronological_growth_blocks']=chronological_growth_blocks(calendar['daily_equity'])
            if any(not block['adaptive_positive'] for block in calendar['chronological_growth_blocks']):
                result['blockers'].append(scenario+'_nonpositive_adaptive_90day_block')
            if calendar['estimated_cost_components']:result['blockers'].append(scenario+'_estimated_expenses')
            from decimal import Decimal
            if any(Decimal(value)>=Decimal('.03') for value in calendar['max_drawdown']):
                result['blockers'].append(scenario+'_drawdown_at_least_3percent')
            if days>14:
                result.setdefault('statistics',{})[scenario]={str(length):paired_circular_bootstrap(
                    calendar['daily_log_changes'],block_days=length) for length in (7,14)}
                if result['statistics'][scenario]['7']['adaptive']['lower_95']<=0:
                    result['blockers'].append(scenario+'_nonpositive_primary_growth_lower_bound')
        result['data_status']='measured_conditional_on_recorded_inputs'
        result['primary_quotes_costs_and_retained_contracts_reconciled']=True
        assert_quote_cohort(root,manifest=manifest,sealed=history is not None)
        result['dated_venue_execution_reconciled']=source_validator.dated_venue_complete and source_validator.venue_packets_seen>0
        if not result['dated_venue_execution_reconciled']:
            result['blockers'].append('dated_venue_execution_evidence_incomplete')
        result['adaptive_publication_provenance_reconciled']=not source_validator.publication_provenance_required
        if source_validator.publication_provenance_required:
            result['blockers'].append('adaptive_publication_provenance_not_reconciled')
    except (OSError,ValueError,TypeError,KeyError,AttributeError,IndexError,RecursionError,sqlite3.Error,yaml.YAMLError,DecimalException) as error:
        result['data_status']='incomplete'
        result['source_error_class']=type(error).__name__
        if str(error)=='nonpositive equity: insolvent':
            result['data_status']='insolvent'
            result['blockers'].append((evaluating_scenario or 'base')+'_insolvency')
        elif str(error)=='missing calendar boundary':
            result['blockers'].append('inception_or_daily_boundary_missing')
        else:
            result['blockers'].append('source_evidence_missing_or_inconsistent')
    result['blockers']=sorted(set(result['blockers']))
    return result


def main():
    parser=argparse.ArgumentParser(description='Read-only primary paired performance report; no promotion')
    parser.add_argument('--root',required=True)
    args=parser.parse_args()
    print(json.dumps(paired_performance_report(Path(args.root)),sort_keys=True,allow_nan=False))


if __name__=='__main__':main()
