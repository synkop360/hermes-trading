"""Invented common-input receipts for accounting tests, never market evidence."""
import base64,hashlib,json
from hermes_trading.paired_paper import _encode,market_source_status
from hermes_trading.loop import _load_yaml
from hermes_trading.market_data import MarketSnapshot,ClosedCandle,timeframe_interval
from hermes_trading.reflection_supervision import _hash
from hermes_trading.paired_risk import initial_risk_state
from hermes_trading.paired_primary import resolve_primary
from venue_fixtures import venue_record


class SyntheticPrimarySources:
    def __init__(self,root,manifest):
        self.manifest=manifest;self.strategies={};self.bindings={};self.archives={};self.configs={}
        self.primary=resolve_primary(manifest)
        for arm in ('adaptive','control'):
            name=self.primary.profile_key(arm)
            path=root/self.primary.profile_directory(manifest,arm);config=json.loads((path/'wallet_account.json').read_text())
            self.configs[name]=config
            self.bindings[name]={'account_hash':_hash(config),'goal_hash':_hash(_encode(_load_yaml(path/'goal.yaml')))}
            self.strategies[name]={}
            for file in [path/'strategy.yaml']+list((path/'history').glob('v*.yaml')):
                strategy=_load_yaml(file);self.strategies[name][str(strategy['version'])]=strategy
            self.archives[name]={file.name:hashlib.sha256(file.read_bytes()).hexdigest()
                                 for file in (path/'history').glob('v*.yaml')}

    def packet(self,at,budgets,bid,*,adaptive_version='1'):
        snapshots={asset:MarketSnapshot(asset,self.manifest['timeframe'],'available','evaluate','synthetic',
            ClosedCandle(at-timeframe_interval(self.manifest['timeframe']),at,bid,bid,bid,bid,None),
            price={'bid':str(bid),'ask':str(bid),'received_at':at.isoformat(),'source':'synthetic_received_bid'},
            venue_rules=venue_record(asset,at))
            for asset in self.manifest['assets']}
        encoded={asset:_encode(dict(vars(snapshot),candle=vars(snapshot.candle))) for asset,snapshot in snapshots.items()}
        costs={}
        for name,config in self.configs.items():
            data={'schema_version':1,'experiment_id':config['experiment_id'],'currency':'EUR',
                  'scope_start':config['activated_at'],'as_of':at.isoformat(),'totals':budgets[name]['cost_provenance']}
            raw=json.dumps(data).encode()
            costs[name]={'present':True,'data':data,'raw_base64':base64.b64encode(raw).decode(),
                         'raw_sha256':hashlib.sha256(raw).hexdigest()}
        return {'schema_version':2,'created_at':at.isoformat(),'manifest_hash':_hash(self.manifest),
            'operational_entries_blocked':False,'risk_state_before_execution':initial_risk_state(self.primary),
            'assets':self.manifest['assets'],'snapshots':encoded,'market_sources':market_source_status(snapshots,at),
            'bindings':self.bindings,'archives':self.archives,'costs':costs,'budgets':_encode(budgets),
            'strategies':_encode({'adaptive':self.strategies[self.primary.profile_key('adaptive')][adaptive_version],
                                  'control':self.strategies[self.primary.profile_key('control')]['1']})}
