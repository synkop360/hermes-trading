"""Read-only reconciliation of primary measurements to original common inputs."""
import base64,hashlib,json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

from hermes_trading.paired_paper import _decode,_encode,_snapshot,market_source_status
from hermes_trading.loop import _load_yaml
from hermes_trading.reflection_supervision import _hash,_time
from hermes_trading.wallet_metrics import _decimal,value_spot_wallet
from hermes_trading.wallet_observer import _costs
from hermes_trading.venue_constraints import parse_venue_constraints,VenueConstraintsError
from hermes_trading.paired_primary import resolve_primary


class PrimarySourceValidator:
    def __init__(self,manifest,profiles):
        self.manifest=deepcopy(manifest)
        self.manifest_hash=_hash(manifest)
        self.primary=resolve_primary(manifest)
        self.paths={self.primary.profile_key(arm):profiles[(arm,self.primary.wallet_eur)][0] for arm in ('adaptive','control')}
        self.configs={self.primary.profile_key(arm):profiles[(arm,self.primary.wallet_eur)][2] for arm in ('adaptive','control')}
        self.bindings={name:{'account_hash':_hash(config),
            'goal_hash':_hash(_encode(_load_yaml(self.paths[name]/'goal.yaml')))}
            for name,config in self.configs.items()}
        self.contracts={}
        self.resolvers={}
        self.publication_provenance_required=False
        self.publication_timeline=None
        self.publication_error_class=None
        self.dated_venue_complete=True
        self.venue_packets_seen=0

    def prepare_publications(self,db,*,now):
        from hermes_trading.publication_timeline import PublicationTimeline
        import sqlite3,yaml
        from decimal import DecimalException
        from hermes_trading.reflection_supervision import WriterNotAuthorized
        self.publication_timeline=None
        self.publication_error_class=None
        profile=next(row for row in self.manifest['profiles'] if row['arm']=='adaptive' and row['wallet_eur']==self.primary.wallet_eur)
        try:
            self.publication_timeline=PublicationTimeline(self.paths[self.primary.profile_key('adaptive')],db,
                profile['strategy_sha256'],now=now)
            self.publication_provenance_required=False
        except (OSError,ValueError,TypeError,KeyError,AttributeError,IndexError,sqlite3.Error,yaml.YAMLError,DecimalException,WriterNotAuthorized) as error:
            self.publication_provenance_required=True
            self.publication_error_class=type(error).__name__

    def _contract(self,name,version):
        key=(name,str(version))
        if key not in self.contracts:
            from hermes_trading.entry_contracts import EntryContractResolver
            current=_load_yaml(self.paths[name]/'strategy.yaml')
            # Resolver validates version syntax before constructing an archive path.
            EntryContractResolver(self.paths[name],current)(self.manifest['assets'][0],version)
            path=self.paths[name]/'strategy.yaml' if str(current['version'])==str(version) else self.paths[name]/'history'/f'v{int(version):04d}.yaml'
            raw=path.read_bytes();strategy=_load_yaml(path)
            self.contracts[key]=(_hash(_encode(strategy)),hashlib.sha256(raw).hexdigest())
        return self.contracts[key]

    def validate_packet(self,packet):
        if self.primary.versioned:
            from hermes_trading.paired_risk import validate_risk_state
            validate_risk_state(packet.get('risk_state_before_execution'),self.primary)
        if type(packet.get('schema_version')) is not int or packet['schema_version'] not in (1,2) or packet.get('manifest_hash')!=self.manifest_hash:
            raise ValueError('original common packet manifest/schema differs')
        if packet.get('assets')!=self.manifest['assets'] or set(packet.get('snapshots',{}))!=set(self.manifest['assets']):
            raise ValueError('original common assets differ')
        snapshots={asset:_snapshot(row) for asset,row in packet['snapshots'].items()}
        if any(snapshot.asset!=asset or snapshot.timeframe!=self.manifest['timeframe'] for asset,snapshot in snapshots.items()):
            raise ValueError('original common snapshot identity differs')
        if market_source_status(snapshots,_time(packet['created_at']),packet_schema=packet['schema_version'])!=packet.get('market_sources'):
            raise ValueError('original common source status differs')
        self.venue_packets_seen+=1
        if packet['schema_version']==1:self.dated_venue_complete=False
        else:
            for asset,snapshot in snapshots.items():
                try:parse_venue_constraints(snapshot.venue_rules,asset=asset,at=_time(packet['created_at']))
                except VenueConstraintsError:self.dated_venue_complete=False
        strategies=_decode(packet['strategies'])
        for name,binding in self.bindings.items():
            if packet.get('bindings',{}).get(name)!=binding:
                raise ValueError('original common account/goal binding differs')
            arm=name.split('_')[0];strategy=strategies[arm]
            semantic,digest=self._contract(name,strategy['version'])
            if semantic!=_hash(_encode(strategy)):
                raise ValueError('original common strategy differs from retained contract')
            if arm=='control' or str(strategy['version'])=='1':
                profile=next(row for row in self.manifest['profiles'] if row['arm']==arm and row['wallet_eur']==self.primary.wallet_eur)
                if digest!=profile['strategy_sha256']:
                    raise ValueError('original strategy differs from frozen baseline')
            elif arm=='adaptive' and self.publication_timeline is None:self.publication_provenance_required=True
        if self.publication_timeline is not None:
            closes={snapshot.candle.close_timestamp for snapshot in snapshots.values() if snapshot.candle is not None}
            if len(closes)>1:raise ValueError('original adaptive signals have mixed causal cutoffs')
            cutoff=next(iter(closes)) if closes else None
            expected=self.publication_timeline.semantic_at(cutoff,_time(packet['created_at']))
            if _hash(_encode(strategies['adaptive']))!=expected:
                raise ValueError('adaptive strategy was unavailable at original causal cutoff')
            release=self.publication_timeline.writer_release
            if release is not None and packet.get('release')!=release:
                raise ValueError('original batch release differs from publication writer')

    def _retained(self,packet,name,version):
        active=str(_decode(packet['strategies'])[name.split('_')[0]]['version'])
        if str(version)!=active:
            _,digest=self._contract(name,version)
            if packet['archives'][name].get(f'v{int(version):04d}.yaml')!=digest:
                raise ValueError('original entry archive receipt differs')

    def _quote(self,packet,asset):
        price=_decode(packet['snapshots'][asset])['price']
        at=_time(packet['created_at']);received=_time(price['received_at'])
        if not timedelta(0)<=at-received<=timedelta(minutes=5):
            raise ValueError('original quote is stale or future-dated')
        bid,ask=(_decimal(price[key]) for key in ('bid','ask'))
        if bid<=0 or ask<bid:raise ValueError('original quote is invalid')
        return bid,ask,received

    def validate_fill(self,packet,name,order,*,holdings=None):
        if _time(order['at'])!=_time(packet['created_at']):
            raise ValueError('original fill has no same-time common packet')
        self._retained(packet,name,order['strategy_version'])
        if order['side']=='buy':
            from hermes_trading.paired_risk import is_halted,validate_risk_state
            risk=packet.get('risk_state_before_execution',{})
            if self.primary.versioned:validate_risk_state(risk,self.primary)
            if (type(packet.get('operational_entries_blocked')) is not bool
                or packet['operational_entries_blocked']
                or type(risk.get('global_halted')) is not bool
                or not isinstance(risk.get('halted_wallets'),list)
                or any(type(wallet) is not int or wallet not in (50,100,150,200) for wallet in risk['halted_wallets'])
                or any(row.get('valid') is not True for row in packet['market_sources'].values())
                or is_halted(risk,name.split('_')[0],self.primary.wallet_eur)):
                raise ValueError('original buy violates shared entry admission')
        if order['side']=='buy' and str(order['strategy_version'])!=str(_decode(packet['strategies'])[name.split('_')[0]]['version']):
            raise ValueError('original entry differs from common strategy epoch')
        bid,ask,_=self._quote(packet,order['asset'])
        slip=_decimal(order['slippage_rate'])
        expected=ask*(1+slip) if order['side']=='buy' else bid*(1-slip)
        if _decimal(order['fill_price'])!=expected:
            raise ValueError('original fill differs from received common quote')
        if order['side']=='buy' and packet['market_sources'][order['asset']]['valid'] is not True:
            raise ValueError('original entry used unusable market source')
        if packet['schema_version']==2:
            venue=parse_venue_constraints(_snapshot(packet['snapshots'][order['asset']]).venue_rules,
                                          asset=order['asset'],at=_time(packet['created_at']))
            venue.validate_order(_decimal(order['quantity']),_decimal(order['fill_price']))
            if order['side']=='sell':
                if holdings is None or order['asset'] not in holdings:
                    raise ValueError('venue remainder verification requires original holdings')
                remainder=holdings[order['asset']]['quantity']-_decimal(order['quantity'])
                if remainder<0:raise ValueError('venue fill exceeds original inventory')
                if remainder>0:venue.validate_order(remainder,_decimal(order['fill_price']))

    def validate_observation(self,packet,name,observation,holdings):
        if packet['schema_version']==2 and (observation.get('execution_venue_protocol')!='dated_rules_v1' or
                                            observation.get('liquidation_complete') is not True):
            raise ValueError('venue liquidation completeness is missing')
        source=packet['costs'][name]
        if source.get('present') is not True or source.get('error'):
            raise ValueError('original raw cost receipt is missing')
        raw=base64.b64decode(source['raw_base64'],validate=True)
        if hashlib.sha256(raw).hexdigest()!=source['raw_sha256'] or _hash(json.loads(raw))!=_hash(source['data']):
            raise ValueError('original raw cost receipt hash/data differs')
        costs,coverage=_costs(self.paths[name],self.configs[name],_time(packet['created_at']),snapshot=source)
        empty=value_spot_wallet(self.configs[name]['starting_balance'],'EUR',[],{},costs,now=_time(packet['created_at']))
        if (empty['net_equity'] is None or empty['cost_provenance']!=observation['cost_provenance']
            or coverage!=observation['cost_coverage']):
            raise ValueError('original cost receipt differs from measured costs')
        if set(observation.get('mark_entry_versions',{}))!=set(holdings):
            raise ValueError('original mark entry versions are incomplete')
        if set(observation.get('mark_provenance',{}))!=set(holdings):
            raise ValueError('original liquidation marks are incomplete')
        for asset,holding in holdings.items():
            mark=observation['mark_provenance'][asset]
            bid,_,received=self._quote(packet,asset)
            if (_decimal(mark['bid'])!=bid or _time(mark['observed_at'])!=received
                or _decimal(mark['exit_fee_rate'])!=holding['entry_fee_rate']
                or _decimal(mark['slippage_rate'])!=holding['slippage_rate']
                or str(observation['mark_entry_versions'][asset])!=holding['strategy_version']):
                raise ValueError('original liquidation mark differs from received quote or entry contract')
            self._retained(packet,name,holding['strategy_version'])
            if packet['schema_version']==2:
                record=_snapshot(packet['snapshots'][asset]).venue_rules
                if _hash(mark.get('venue_rules'))!=_hash(record):raise ValueError('venue liquidation source differs')
                venue=parse_venue_constraints(record,asset=asset,at=_time(packet['created_at']))
                venue.validate_order(holding['quantity'],bid*(1-holding['slippage_rate']))

    def stressed_prebatch_equity(self,packet,name,engine):
        """Freeze risk equity before fills; existing inventory uses current bids."""
        budget=_decode(packet['budgets'][name])
        total=_decimal(budget['known_accrued_costs'])
        equity=engine.stressed_cash-2*total
        for asset,holding in engine.holdings.items():
            bid,_,_=self._quote(packet,asset)
            equity+=holding['quantity']*bid*(1-2*holding['slippage_rate'])*(1-2*holding['entry_fee_rate'])
        return equity

    def validate_configured_stress(self,packet,name,order,engine,equity):
        from hermes_trading.entry_contracts import EntryContractResolver
        from hermes_trading.paired_cost_stress import configured_order_feasibility
        if name not in self.resolvers:
            self.resolvers[name]=EntryContractResolver(self.paths[name],_load_yaml(self.paths[name]/'strategy.yaml'))
        strategy=self.resolvers[name](order['asset'],order['strategy_version'])
        exposure=sum((row['quantity']*row['stress_entry'] for row in engine.holdings.values()),_decimal(0))
        cash=engine.stressed_cash-2*_decimal(_decode(packet['budgets'][name])['known_accrued_costs'])
        holding=engine.holdings.get(order['asset'])
        remainder=None if order['side']=='buy' or holding is None else holding['quantity']-_decimal(order['quantity'])
        result=configured_order_feasibility(order,strategy,_snapshot(packet['snapshots'][order['asset']]),
            equity=equity,spendable_cash=cash,existing_exposure=exposure,
            open_positions=len(engine.holdings),remaining_quantity=remainder,venue_required=packet['schema_version']==2)
        if order['side']=='buy' and holding is not None:
            result['failures'].append('existing_symbol_inventory');result['feasible']=False
        return result
