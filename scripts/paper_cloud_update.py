"""Resume one exact notification-only release transition before child startup."""
import os
from pathlib import Path
from scripts import paper_release_transition as transition
from scripts.paper_cloud_install import recover_journals
from scripts.paper_archive_repack import repack_archives
from hermes_trading.reflection_supervision import _hash


def update_installed(project,source_plan,*,fault=None):
    project=transition._safe_root(Path(project).absolute());root=project/'state'
    if os.environ.get('HERMES_TRADING_MODE')!='paper' or os.environ.get('HERMES_TRADING_ALLOW_REAL_ORDERS')!='false':
        raise ValueError('explicit paper-only source update required')
    if any(os.environ.get('RAILWAY_'+k.upper()+'_ID')!=v for k,v in transition.TARGET.items()):
        raise ValueError('source update destination differs')
    transition.validate_release_plan(source_plan)
    if source_plan['old_commit']!=transition.DISCORD_FORMAT_BASE_COMMIT:
        raise ValueError('exact notification correction predecessor required')
    with transition.volume_cutover_layout(root,live_supervisor=True) as auxiliary:
        installed=transition._read(auxiliary/'installed.json')
        if (installed.get('status')!='installed' or installed.get('experiment_id')!=source_plan['target']['experiment_id']):
            raise ValueError('retained cloud installation required')
        supervisor=root/'paired-supervisor'/installed['experiment_id']/'adaptive_200'
        launch=root/'cloud-supervisor-launch.json';audit=auxiliary/'discord-format'
        options=dict(worker_root=root,supervisor_root=supervisor,launch_file=launch,audit_dir=audit)
        intent=audit/'intent.json'
        if installed.get('release_hash')==_hash(source_plan['new']):
            plan=transition._read(intent)['plan']
            if plan['release_plan']!=source_plan:raise ValueError('retained source update plan differs')
            layout=transition._layout(**options)
            transition._intent(plan,layout);transition._verify_rollback(plan,layout)
            receipt=transition._read(audit/'receipt.json')
            if (receipt.get('status')!='completed' or receipt.get('transition_id')!=plan['transition_id'] or
                    receipt.get('root_binding')!=transition._root_binding(layout) or
                    installed.get('source_update')!=plan['transition_id']):
                raise ValueError('retained source update completion differs')
            return {'kind':'paper_cloud_source_update','status':'already_updated'}
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
            approval_ref='user-2026-11-12-discord-strategy-index',fault=fault)
        if result['status']!='completed':raise ValueError('source update completion required')
        transition._save(auxiliary/'installed.json',dict(installed,release_hash=_hash(source_plan['new']),source_update=plan['transition_id']))
        return {'kind':'paper_cloud_source_update','status':'updated'}
