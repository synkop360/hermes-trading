"""Resume exact chained source-only release transitions before child startup.

Each reviewed step has its own predecessor commit, audit directory and
approval reference. Steps are applied in order: the Discord notification
correction (d045e98 -> 121069c), then the paper pilot (121069c -> pilot).
"""
import os
from pathlib import Path
from scripts import paper_release_transition as transition
from scripts.paper_cloud_install import recover_journals
from scripts.paper_archive_repack import repack_archives
from hermes_trading.reflection_supervision import _hash

STEPS={
    transition.DISCORD_FORMAT_BASE_COMMIT:('discord-format','user-2026-11-12-discord-strategy-index'),
    transition.PILOT_BASE_COMMIT:('pilot-source','user-2026-11-13-lat-116-chained-transition'),
}


def _completed(plan,audit,layout,installed):
    receipt=transition._read(audit/'receipt.json')
    if receipt.get('status')!='completed' or receipt.get('transition_id')!=plan['transition_id']:
        raise ValueError('retained source update completion differs')
    if layout is not None and (receipt.get('root_binding')!=transition._root_binding(layout) or
            installed.get('source_update')!=plan['transition_id']):
        raise ValueError('retained source update completion differs')


def update_installed(project,source_plan,*,successor=None,fault=None):
    project=transition._safe_root(Path(project).absolute());root=project/'state'
    if os.environ.get('HERMES_TRADING_MODE')!='paper' or os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false':
        raise ValueError('explicit paper-only source update required')
    if any(os.environ.get('RAILWAY_'+k.upper()+'_ID')!=v for k,v in transition.TARGET.items()):
        raise ValueError('source update destination differs')
    transition.validate_release_plan(source_plan)
    if source_plan['old_commit'] not in STEPS:
        raise ValueError('exact reviewed source update predecessor required')
    name,approval=STEPS[source_plan['old_commit']]
    if successor is not None:
        transition.validate_release_plan(successor)
        if successor['old_commit'] not in STEPS or successor['old']!=source_plan['new'] or successor['target']!=source_plan['target']:
            raise ValueError('chained source update successor differs')
    with transition.volume_cutover_layout(root,live_supervisor=True) as auxiliary:
        installed=transition._read(auxiliary/'installed.json')
        if (installed.get('status')!='installed' or installed.get('experiment_id')!=source_plan['target']['experiment_id']):
            raise ValueError('retained cloud installation required')
        supervisor=root/'paired-supervisor'/installed['experiment_id']/'adaptive_200'
        launch=root/'cloud-supervisor-launch.json';audit=auxiliary/name
        options=dict(worker_root=root,supervisor_root=supervisor,launch_file=launch,audit_dir=audit)
        intent=audit/'intent.json'
        if successor is not None:
            # A later reviewed step has started or completed: this step must be
            # complete under its own retained evidence, its root binding is
            # now owned by the successor step.
            later=auxiliary/STEPS[successor['old_commit']][0]/'intent.json'
            if installed.get('release_hash')==_hash(successor['new']) or later.exists():
                plan=transition._read(intent)['plan']
                if plan['release_plan']!=source_plan:raise ValueError('retained source update plan differs')
                _completed(plan,audit,None,installed)
                return {'kind':'paper_cloud_source_update','step':name,'status':'superseded'}
        if installed.get('release_hash')==_hash(source_plan['new']):
            plan=transition._read(intent)['plan']
            if plan['release_plan']!=source_plan:raise ValueError('retained source update plan differs')
            layout=transition._layout(**options)
            transition._intent(plan,layout);transition._verify_rollback(plan,layout)
            _completed(plan,audit,layout,installed)
            return {'kind':'paper_cloud_source_update','step':name,'status':'already_updated'}
        if installed.get('release_hash')!=_hash(source_plan['old']):
            raise ValueError('installed source update predecessor differs')
        # All previous children have exited. Preserve raw journal evidence and
        # close it using the existing SQLite-owned recovery, never unlink WALs.
        recover_journals(root,auxiliary)
        repack_archives(auxiliary)
        if intent.exists():
            plan=transition._read(intent)['plan']
            if plan['release_plan']!=source_plan:raise ValueError('pending source update plan differs')
        else:
            identity=transition._identity(transition._layout(root,supervisor,launch),check_economics=True)
            plan=transition.prepare_plan(root,supervisor,launch,release_plan=source_plan,expected_identity=identity)
        result=transition.apply_transition(plan,**options,apply=True,reconcile=intent.exists(),
            approval_ref=approval,fault=fault)
        if result['status']!='completed':raise ValueError('source update completion required')
        transition._save(auxiliary/'installed.json',dict(installed,release_hash=_hash(source_plan['new']),source_update=plan['transition_id']))
        return {'kind':'paper_cloud_source_update','step':name,'status':'updated'}
