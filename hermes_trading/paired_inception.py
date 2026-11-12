"""Prospective common opening receipt, never a historical balance backfill."""
from datetime import timedelta
from contextlib import closing
import json,sqlite3
from pathlib import Path

from hermes_trading.paired_packet_storage import load_packet,store_record
from hermes_trading.paper_publication import _connect
from hermes_trading.reflection_supervision import _hash,_now,_time
from hermes_trading.wallet_metrics import value_spot_wallet
from hermes_trading.wallet_observer import _costs,record_wallet_observation
from hermes_trading.paired_primary import resolve_primary


def _context(root):
    from hermes_trading.paired_paper import _profiles
    root=Path(root)
    manifest=json.loads((root/'manifest.json').read_text())
    return root,manifest,_profiles(root,manifest)


def _connection_has_rows(db,tables):
    names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return any(db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone() is not None
               for table in tables if table in names)


def _has_rows(path,tables):
    if not path.exists():return False
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        return _connection_has_rows(db,tables)
    finally:db.close()


def _validate(receipt,digest,manifest,profiles):
    if _hash(receipt)!=digest or receipt.get('schema_version')!=1:
        raise ValueError('opening receipt hash/schema differs')
    names={f'{arm}_{wallet}' for arm,wallet in profiles}
    if receipt.get('manifest_hash')!=_hash(manifest) or set(receipt.get('accounts',{}))!=names:
        raise ValueError('opening manifest/account binding differs')
    at=_time(receipt['at'])
    if manifest.get('execution_venue_protocol')=='dated_rules_v1':
        from hermes_trading.venue_candidate import validate_activation_venue
        root=next(iter(profiles.values()))[0].parent
        venue=validate_activation_venue(root,at=at)
        if receipt.get('venue_activation_hash')!=_hash(venue):raise ValueError('opening venue binding differs')
    for (arm,wallet),(path,profile,config) in profiles.items():
        observation=receipt['accounts'][f'{arm}_{wallet}']
        if (observation.get('account_config_hash')!=_hash(config) or
            observation.get('experiment_id')!=config['experiment_id'] or
            _time(observation['at'])!=at or
            not timedelta(0)<=at-_time(config['activated_at'])<=timedelta(minutes=2)):
            raise ValueError('opening account/time binding differs')
    return receipt


def read_paired_inception(root,*,database_factory=None,check=lambda:None):
    root,manifest,profiles=_context(root)
    path=profiles[('adaptive',resolve_primary(manifest).wallet_eur)][0]/'paper_publications.sqlite'
    if not path.exists():raise ValueError('authoritative opening receipt is missing')
    def connection(path):
        check()
        return (database_factory(path,check=check) if database_factory is not None else
                closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)))
    with connection(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='paired_inception'").fetchone():
            raise ValueError('authoritative opening receipt is missing')
        row=db.execute('SELECT payload,receipt_hash FROM paired_inception WHERE id=1').fetchone()
        if row is None:raise ValueError('authoritative opening receipt is missing')
        receipt=_validate(load_packet(db,row[0]),row[1],manifest,profiles)
        for (arm,wallet),(path,profile,config) in profiles.items():
            check()
            journal=path/'wallet_equity.sqlite'
            if not journal.exists():raise ValueError('opening wallet copies are incomplete')
            with connection(journal) as account_db:
                recorded=account_db.execute('SELECT payload FROM observations WHERE at=?',(receipt['at'],)).fetchone()
                if recorded is None or load_packet(account_db,recorded[0])!=receipt['accounts'][f'{arm}_{wallet}']:
                    raise ValueError('opening wallet copy differs or is missing')
        check()
        return receipt


def initialize_paired_inception(root,*,clock=None):
    """Use the current clock for fresh accounts; recover only committed receipts."""
    root,manifest,profiles=_context(root)
    at=_now(clock() if clock is not None else None)
    primary=profiles[('adaptive',resolve_primary(manifest).wallet_eur)][0]
    db=_connect(primary/'paper_publications.sqlite')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS paired_inception '
                   '(id INTEGER PRIMARY KEY CHECK(id=1),payload BLOB NOT NULL,receipt_hash TEXT NOT NULL)')
        row=db.execute('SELECT payload,receipt_hash FROM paired_inception WHERE id=1').fetchone()
        if row:
            receipt=_validate(load_packet(db,row[0]),row[1],manifest,profiles)
        else:
            venue=None
            if manifest.get('execution_venue_protocol')=='dated_rules_v1':
                from hermes_trading.venue_candidate import validate_activation_venue
                venue=validate_activation_venue(root,at=at)
            if _connection_has_rows(db,('paired_batches','publications')):
                raise ValueError('existing shared history requires reconciliation')
            accounts={}
            activation={_time(config['activated_at']) for path,profile,config in profiles.values()}
            if len(activation)!=1 or not timedelta(0)<=at-next(iter(activation))<=timedelta(minutes=2):
                raise ValueError('fresh common activation is required; cannot backfill inception')
            for (arm,wallet),(path,profile,config) in profiles.items():
                if (_has_rows(path/'trading.db',('positions','orders','decisions','order_intents')) or
                    _has_rows(path/'wallet_equity.sqlite',('observations',))):
                    raise ValueError('existing wallet history requires reconciliation')
                costs,coverage=_costs(path,config,at)
                observation=value_spot_wallet(config['starting_balance'],'EUR',[],{},costs,now=at)
                if observation['missing'] or observation['net_equity'] is None:
                    raise ValueError('complete opening cost coverage is required')
                observation.update(experiment_id=config['experiment_id'],activated_at=config['activated_at'],
                    account_config_hash=_hash(config),cost_coverage=coverage,mark_entry_versions={})
                accounts[f'{arm}_{wallet}']=observation
            receipt={'schema_version':1,'at':at.isoformat(),'manifest_hash':_hash(manifest),
                     'basis':'fresh_empty_standalone_paper_wallets','accounts':accounts}
            if venue is not None:receipt['venue_activation_hash']=_hash(venue)
            db.execute('INSERT INTO paired_inception VALUES (1,?,?)',
                       (store_record(db,receipt),_hash(receipt)))
        db.commit()
        # Reacquire the same writer lease while copying the authoritative receipt.
        db.execute('BEGIN EXCLUSIVE')
        for (arm,wallet),(path,profile,config) in profiles.items():
            observation=receipt['accounts'][f'{arm}_{wallet}']
            journal=path/'wallet_equity.sqlite'
            if journal.exists():
                account_db=sqlite3.connect(journal.resolve().as_uri()+'?mode=ro',uri=True)
                try:
                    recorded=account_db.execute('SELECT payload FROM observations WHERE at=?',(receipt['at'],)).fetchone()
                    if recorded is not None:
                        if load_packet(account_db,recorded[0])!=observation:
                            raise ValueError('opening wallet copy differs')
                        continue
                    if account_db.execute('SELECT 1 FROM observations LIMIT 1').fetchone():
                        raise ValueError('missing opening copy behind later history requires reconciliation')
                finally:account_db.close()
            record_wallet_observation(path,observation,now=_time(receipt['at']))
        db.commit()
        return receipt
    finally:db.close()
