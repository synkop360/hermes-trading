"""Render synthetic notifications through the real reporter and an in-memory poster."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import httpx
from hermes_trading.notifications import PaperDiscordReporter
from tests.test_paper_notifications import close, envelope, eventually, identity, settings

OUT = Path(__file__).resolve().parent

async def render():
    sent = []
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)

    async def poster(url, payload):
        sent.append(copy.deepcopy(payload))

    with tempfile.TemporaryDirectory() as directory:
        reporter = PaperDiscordReporter(directory, identity(), settings(), post_json=poster, clock=lambda: now)
        value = envelope(at=now)
        value.update(status='running', model_ready=True,
                     entry_admission={'enabled': True, 'blocked': True, 'reasons': ['supervisor_error_halt']})
        for name, row in value['accounts'].items():
            size = int(name.split('_')[1])
            row['observation'].update(net_equity=str(size - 1) + '.1234567890123456789',
                closed_net_pnl='0', known_accrued_costs='0.8765432109876543211',
                open_basis='0', open_liquidation_value='0', open_entry_fees='0',
                cost_provenance={category: {'quality': 'estimated' if category == 'hosting' else 'actual',
                    'amount': '0.8765432109876543211' if category == 'hosting' else '0'}
                    for category in ('hosting', 'ai', 'tools', 'funding')})
            row['decisions'][0].update(action='no_trade', reason='supervisor_error_halt')
        reporter.offer(value)
        task = asyncio.create_task(reporter.run())
        try:
            await eventually(lambda: reporter._state.get('startup') is True, timeout=5)
            summary = copy.deepcopy(sent)
            sent.clear()
            now += timedelta(minutes=1)
            trade = copy.deepcopy(value)
            trade.update(at=now.isoformat(), observed_at=now.isoformat(), batch_id='batch-2', input_hash='input-2', completed_batches=2)
            for row in trade['accounts'].values():
                row['observed_at'] = now.isoformat()
                row['observation']['at'] = now.isoformat()
            event = close('synthetic-position-1', pnl='-0.0042')
            event['closed_at'] = (now - timedelta(seconds=1)).isoformat()
            trade['accounts']['adaptive_200']['closes'] = [event]
            reporter.offer(trade)
            await eventually(lambda: bool(reporter._state.get('events')), timeout=5)
            closes = copy.deepcopy(sent)
            sent.clear()
            now += timedelta(minutes=1)
            operational = copy.deepcopy(trade)
            operational.update(kind='operational', at=now.isoformat(), accounts={}, status='ok')
            reporter.offer(operational)
            await eventually(lambda: any('Operational state change' in p['content'] for p in sent), timeout=5)
            status = copy.deepcopy(sent)
            sent.clear()
            # A separate synthetic case demonstrates zero known costs with unknown total costs.
            missing = copy.deepcopy(value)
            missing['accounts']['adaptive_200']['observation'].update(net_equity=None,
                known_accrued_costs='0', missing=['cost:hosting'],
                cost_provenance={'hosting': {'quality': 'missing', 'amount': None}})
            reporter._current = missing
            reporter._snapshot = missing
            unknown_parts = reporter._parts('synthetic-unknown', reporter._summary(['unknown costs example']))
            unknown = [{'content': content} for _, content in unknown_parts]
        finally:
            task.cancel()
            try:
                await asyncio.wait_for(task, 3)
            except asyncio.CancelledError:
                pass
    examples = {'summary': summary, 'close': closes, 'status': status, 'unknown_costs': unknown}
    for messages in examples.values():
        assert 1 <= len(messages) <= 4
        assert all(len(p['content']) <= 1900 for p in messages)
        assert all(p['content'].count('**Observed at:**') == 1 for p in messages)
    text = '\n'.join(p['content'] for p in summary)
    assert text.count('**Wallet:**') == 8 and text.count('**adaptive minus control ') == 4
    assert '**Wallet:** EUR 199.12' in text
    assert '**Realized P&L:** EUR -<0.01' in closes[0]['content']
    assert '**Wallet:** unknown | **Closed P&L:** EUR 0.00 | **Known costs:** EUR 0.00' in unknown[0]['content']
    receipt = {'synthetic_only': True, 'transport': 'in_memory_HTTP_forbidden',
               'source_sha256': hashlib.sha256((ROOT / 'hermes_trading/notifications.py').read_bytes()).hexdigest(),
               'lengths': {key: [len(p['content']) for p in messages] for key, messages in examples.items()}, **examples}
    (OUT / 'rendered-preview-v2.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    lines = ['# Discord message preview — synthetic only', '',
        '**All numbers, times, wallets and trades below are synthetic. This formatting is not deployed.**', '',
        'These are actual formatter outputs captured in memory with HTTP disabled. The entry pause is prominent; the primary €200 wallet leads all eight wallets and four comparisons. Money is rounded for display only. Signed amounts below one cent stay visible, and missing costs never become zero.', '',
        'The worker being healthy or model access being available does not prove a strategy review occurred. Daily/weekly messages remain dated current snapshots, with no interval return, Sharpe or profitability claim.', '']
    titles = {'status': 'Short status alert', 'summary': 'Current wallet summary',
              'close': 'Trade close alert — sub-cent loss', 'unknown_costs': 'Missing-cost example — known subtotal is not total cost'}
    for key in ('status', 'summary', 'close', 'unknown_costs'):
        messages = examples[key]
        lines.extend([f'## {titles[key]}', ''])
        for i, message in enumerate(messages, 1):
            lines.extend([f'### Part {i}/{len(messages)} · {len(message["content"])} characters', '', message['content'], ''])
    lines.extend(['## Limits and preserved behavior', '',
        '- Maximum four parts per envelope, 1900 characters per part.',
        '- All six native money fields and per-category actual/estimated/missing cost quality remain represented.',
        '- Accounting, event identities, notification frequency, retries and confirmation markers are unchanged.',
        '- No Discord send, deployment, live state edit or trading change was performed.', ''])
    (ROOT / 'docs/PAPER_DISCORD_MESSAGE_PREVIEW.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'source_sha256': receipt['source_sha256'], 'lengths': receipt['lengths']}))

if __name__ == '__main__':
    with patch.object(httpx.HTTPTransport, 'handle_request', side_effect=AssertionError('real HTTP forbidden')), \
         patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=AssertionError('real HTTP forbidden')):
        asyncio.run(render())
