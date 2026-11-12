"""Explicit paper container entrypoint; never starts the legacy writer."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from hermes_trading.paired_inception import initialize_paired_inception,read_paired_inception
from hermes_trading.paired_primary import resolve_primary
from hermes_trading.paired_worker import run_worker,_model_ready
from hermes_trading.paper_safety import load_policy
from hermes_trading.reflection_supervision import assert_writer_owner,_now,_json,_hash


def _reporter_finished(task):
    """Observe optional task failures without changing worker health or its result."""
    if task.cancelled():return
    error=task.exception()
    if error is not None:
        print('paper notification sender failed: '+type(error).__name__,flush=True)


async def run_paper(root,*,env=None):
    env=os.environ if env is None else env
    if (env.get('HERMES_TRADING_MODE','').strip().lower()!='paper' or
            env.get('HERMES_TRADING_ALLOW_REAL_ORDERS','').strip().lower()!='false'):
        raise ValueError('explicit paper mode with real orders disabled is required')
    root=Path(root).resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    limits=json.loads((root/'worker_limits.json').read_text(encoding='utf-8'))
    if manifest.get('status')!='paper_ready' or manifest.get('execution_enabled') is not True:
        raise ValueError('approved activated paper configuration is required')
    if any(key.startswith('archive_') and value is not None for key,value in limits.items()):
        raise ValueError('archive extensions must remain disabled for the paper beta')
    selection=resolve_primary(manifest)
    if not selection.versioned or selection.wallet_eur!=200:
        raise ValueError('paper beta requires the preregistered EUR200 primary')
    if not manifest.get('assets') or not set(manifest['assets'])<={'BTC/EUR','ETH/EUR','XRP/EUR'}:
        raise ValueError('paper beta requires the declared EUR spot markets')
    primary=root/selection.profile_directory(manifest,'adaptive')
    owner=assert_writer_owner(primary,'external_llm')
    if owner is None or load_policy(root,owner['experiment_id']) is None or limits.get('daily_backup') is None:
        raise ValueError('paper beta requires one external writer and minimum safety/backup policies')
    if (root/'worker_runtime.sqlite').is_file():
        # An existing worker must retain its original opening. Expired model
        # access blocks new exposure in the worker, while exits keep running.
        read_paired_inception(root)
    elif not _model_ready(primary,owner,_now()):
        raise ValueError('fresh authorized model access is required before inception')
    # Fresh activation/venue/cost requirements are checked by the original
    # initializer. Restart reads the authoritative receipt without resetting it.
    opening=initialize_paired_inception(root)
    reporter=None
    if env.get('HERMES_NOTIFY_ENABLED','').strip().lower()=='true':
        try:
            from hermes_trading import notifications
            settings=notifications.resolve_notification_settings(env)
            identity=dict(experiment_id=owner['experiment_id'],opening_at=opening['at'],
                opening_hash=_hash(opening),manifest_hash=_hash(manifest),primary_wallet_eur=200,
                account_hashes={name:row['account_config_hash'] for name,row in opening['accounts'].items()})
            candidate=notifications.PaperDiscordReporter(root,identity,settings)
            if candidate.enabled:reporter=candidate
        except Exception as error:
            print('paper notification configuration unavailable: '+type(error).__name__,flush=True)
    if reporter is None:return await run_worker(root)
    task=asyncio.create_task(reporter.run(),name='paper-discord-reporter')
    task.add_done_callback(_reporter_finished)
    try:
        return await run_worker(root,observer=reporter.offer)
    finally:
        # run_worker has already unwound its financial leases. Notification-only
        # shutdown never waits for network delivery or retries indefinitely.
        task.cancel()
        done,_=await asyncio.wait({task},timeout=2)
        if not done:print('paper notification sender shutdown pending',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    args=parser.parse_args()
    print(_json(asyncio.run(run_paper(args.root))))


if __name__=='__main__':main()
