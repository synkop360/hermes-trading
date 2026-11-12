"""Read-only, receipt-bound strategy availability at original signal cutoffs."""
import base64,hashlib,json
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
from pathlib import Path
import yaml

from hermes_trading.loop import _load_yaml
from hermes_trading.paired_paper import _encode
from hermes_trading.paper_publication import _validate_request
from hermes_trading.reflection_supervision import _hash,_now,_time


class PublicationTimeline:
    def __init__(self,state_dir,db,baseline_sha256,*,now=None):
        path=Path(state_dir);view=_now(now)
        current_raw=(path/'strategy.yaml').read_bytes();current=_load_yaml(path/'strategy.yaml')
        self.nodes=[];self.writer_release=None
        table=db.execute("SELECT 1 FROM sqlite_master WHERE name='publications'").fetchone()
        records=[] if table is None else list(db.execute(
            'SELECT rowid,review_id,request_hash,request,base_bytes,receipt FROM publications ORDER BY rowid'))
        if not records:
            if hashlib.sha256(current_raw).hexdigest()!=baseline_sha256 or int(str(current['version']))!=1:
                raise ValueError('missing publication chain for changed strategy')
            self.baseline=current
            self.baseline_semantic=_hash(_encode(current))
            return
        ordered=[]
        for sequence,review_id,request_hash,raw_request,base_bytes,raw_receipt in records:
            if raw_receipt is None:raise ValueError('unfinished publication prevents availability verification')
            request=json.loads(raw_request);typed=json.loads(raw_request,parse_float=Decimal)
            receipt=json.loads(raw_receipt)
            if request.get('review_id')!=review_id or _hash(request)!=request_hash:
                raise ValueError('publication request hash/identity differs')
            release=request['writer_release']
            if not isinstance(release,dict) or not release or any(not isinstance(value,str) or len(value)!=64 for value in release.values()):
                raise ValueError('publication writer release is missing or malformed')
            if self.writer_release is None:self.writer_release=release
            elif release!=self.writer_release:raise ValueError('publication writer release changed within experiment')
            # Historical inspection uses the recorded runtime; actual publishing
            # keeps its default current-runtime guard and accepts no override.
            _validate_request(path,request,{'HERMES_TRADING_MODE':'paper'},writer_release=release)
            original=base64.b64decode(base_bytes,validate=True)
            if hashlib.sha256(original).hexdigest()!=request['base_sha256']:
                raise ValueError('publication original base bytes differ')
            candidate=yaml.safe_dump(request['candidate'],sort_keys=True).encode()
            expected={'schema_version':1,'status':'published','review_id':review_id,
                'experiment_id':request['experiment_id'],'request_hash':request_hash,
                'base_sha256':request['base_sha256'],'candidate_sha256':hashlib.sha256(candidate).hexdigest(),
                'goal_sha256':request['goal_sha256'],'policy_hash':request['policy_hash']}
            if any(receipt.get(key)!=value for key,value in expected.items()):
                raise ValueError('publication receipt differs from original request')
            stamp=_time(receipt['published_at'])
            if not _time(request['review']['started_at'])<=stamp<=view:
                raise ValueError('publication time precedes review or is future-dated')
            archive=path/'history'/f"v{int(str(request['base_strategy']['version'])):04d}.yaml"
            if archive.read_bytes()!=original or _hash(_encode(_load_yaml(archive)))!=_hash(_encode(typed['base_strategy'])):
                raise ValueError('publication archive differs from original base')
            ordered.append((stamp,sequence,request,typed,original,candidate))
        ordered.sort(key=lambda item:(item[0],item[1]))
        if hashlib.sha256(ordered[0][4]).hexdigest()!=baseline_sha256:
            raise ValueError('publication chain does not start at original baseline')
        self.baseline=ordered[0][3]['base_strategy']
        self.baseline_semantic=_hash(_encode(self.baseline))
        prior_raw=ordered[0][4];prior_semantic=self.baseline_semantic
        for stamp,sequence,request,typed,original,candidate in ordered:
            if original!=prior_raw or _hash(_encode(typed['base_strategy']))!=prior_semantic:
                raise ValueError('publication chain is discontinuous')
            semantic=_hash(_encode(typed['candidate']))
            self.nodes.append((stamp,typed['candidate'],semantic))
            prior_raw=candidate;prior_semantic=semantic
        if current_raw!=prior_raw or _hash(_encode(current))!=prior_semantic:
            raise ValueError('current strategy differs from final publication receipt')

    def _selected(self,closed_at,created_at):
        created=_now(created_at) if isinstance(created_at,datetime) else _time(created_at)
        cutoff=created if closed_at is None else (_now(closed_at) if isinstance(closed_at,datetime) else _time(closed_at))
        if cutoff>created:raise ValueError('future closed signal cannot select a strategy')
        strategy,semantic=self.baseline,self.baseline_semantic
        for published,candidate,digest in self.nodes:
            eligible=published<=cutoff if closed_at is None else published<cutoff
            if not eligible:break
            strategy,semantic=candidate,digest
        return strategy,semantic

    def strategy_at(self,closed_at,created_at):
        return deepcopy(self._selected(closed_at,created_at)[0])

    def semantic_at(self,closed_at,created_at):
        return self._selected(closed_at,created_at)[1]
