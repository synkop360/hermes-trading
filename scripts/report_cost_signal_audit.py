"""Render audit findings from saved measurements; does not run research."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'research/runs/cost-signal-audit'


def pct(value):
    return '—' if value is None else f'{value*100:.3f}%'


def main():
    audit=json.loads((OUT/'account_cost_audit.json').read_text())
    study=json.loads((OUT/'signal_results.json').read_text())
    lines=['# Account costs and signal quality — 2026-09-13','',
           '**Finding:** the frozen RSI entry rule shows no repeatable positive advantage in this study. The primary final-quarter signals have negative mean raw returns at every tested horizon, and all primary period/horizon combinations lose after modeled costs. Lower fees alone do not establish a profitable strategy.','',
           'This audit does not change the live strategy. No real orders, private account requests, model training or parameter optimization were performed. The user reports actual fees and hosting/AI bills as unknown.','',
           '## Cost evidence','',
           '| Item | Finding |','|---|---|',
           '| Kraken account fee tier | Unverified: no configured exchange credentials were found. |',
           '| Research fee assumption | 0.8% taker per side, from the published tier-one schedule; not verified for this account. |',
           '| Hosting bill | Unknown; no configured monthly amount. |',
           '| AI bill | Unknown; token usage is recorded but rates/billed amounts are missing. |','',
           'Published reference: [Kraken fee schedule](https://www.kraken.com/features/fee-schedule). Account-specific rates can be read using [Kraken TradeVolume](https://docs.kraken.com/api-reference/account-data/get-trade-volume) when suitable account access is available. Public pair status does not establish regional or account eligibility.','']
    ledger=audit['cost_ledger']
    lines += [f"The active local ledger covers {ledger['first']} to {ledger['last']}. It has {ledger['invalid_rows']} malformed rows. Experimental state directories were excluded to avoid treating duplicated trial usage as an actual bill.",'']
    for model,usage in audit['ai_usage_by_model'].items():
        lines += [f"Recorded model `{model}`: {usage['records']} calls, {usage['input_tokens']:,} input tokens and {usage['output_tokens']:,} output tokens. Missing usage fields: {usage['missing_usage_fields']}. These records do not establish what was charged.",'',
                  f"If input/output rates are quoted per million tokens, the recorded token cost would be **{usage['input_tokens']/1e6:.6f} × input rate + {usage['output_tokens']/1e6:.6f} × output rate**, before any caching, discounts, subscription allocation or billing adjustments. No price has been invented for this model.",'']
    spreads={}
    for observation in audit['public_observations']:
        for pair,value in observation.get('spreads_bps',{}).items():
            spreads.setdefault(pair,[]).append(value)
    lines += ['### Public spread observations','',
              'Three brief public quote observations during this audit. These are not historical spread/slippage estimates or evidence of reliable maker fills. One basis point is 0.01%.','',
              '| Pair | Minimum spread, bps | Maximum spread, bps |','|---|---:|---:|']
    for pair,values in sorted(spreads.items()):
        lines.append(f'| {pair} | {min(values):.4f} | {max(values):.4f} |')
    lines += ['', 'The assumed taker fee is 80 bps per side; quoted spreads in these samples are much smaller. Slippage can increase during volatility and is separate from the quoted spread. Raw public responses and timestamps are retained in `account_cost_audit.json`.','',
              '## Signal study','',
              'The frozen corrected 15m RSI entry rule is evaluated without an existing position and before cost/minimum gates. Outcomes start at the next open and finish at fixed 1/4/12/24-hour horizons. This is an event study, not a tradable portfolio backtest: overlapping opportunities, exposure, stops and position ownership are not simulated.','',
              'Recent development: October 2025–February 2026; evaluation: March–May 2026; final check: June–August 2026. Older stress: January 1–July 26, 2022. Each observation requires a complete contiguous 24h future window inside its partition, so all horizons share the same cohort. All periods were previously available to this project and are not certified unseen.','',
              '### Primary subset: signals at least 24h apart per asset','',
              'Net returns assume 0.8% taker commission and 0.1% slippage on each side. The average is per hypothetical trade, not account return.','',
              '| Period | Hold hours | Signals | Mean raw return | Mean net return | Net win rate | Mean favorable excursion | Mean adverse excursion |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for label,summary in study.items():
        for hours,r in summary['subsets']['nonoverlap'].items():
            lines.append(f"| {label} | {hours} | {r['count']} | {pct(r.get('mean_gross_return'))} | {pct(r.get('mean_net_return'))} | {pct(r.get('net_win_rate'))} | {pct(r.get('mean_favorable_excursion'))} | {pct(r.get('mean_adverse_excursion'))} |")
    lines += ['', 'Favorable/adverse excursions are the best/worst prices observed during the horizon. They are not exit signals and cannot be assumed achievable; their ordering within an OHLC bar is unknown.','',
              '### Matched comparison','',
              'Controls are non-signal entry times for the same asset, ISO week, UTC four-hour clock bucket, fixed ATR/price band and trend state. The nearest eligible control is at least 24h earlier, so its outcome ends before signal entry. Matching never uses future returns. Unmatched signals remain in the absolute-return table but are excluded from paired differences. Different matched pairs can still share/reuse control periods.','',
              '| Period | Hold hours | Matched signals | Distinct controls | Occupied weeks | Signal minus control raw return | Descriptive 95% interval |','|---|---:|---:|---:|---:|---:|---|']
    for label,summary in study.items():
        for hours,r in summary['subsets']['nonoverlap'].items():
            interval=r.get('paired_gross_difference_interval_95')
            bounds='insufficient clusters' if interval is None else f'{pct(interval[0])} to {pct(interval[1])}'
            lines.append(f"| {label} | {hours} | {r.get('matched',0)} | {r.get('unique_controls',0)} | {r.get('matched_calendar_weeks',0)} | {pct(r.get('paired_gross_difference'))} | {bounds} |")
    lines += ['', 'Intervals use 2,000 fixed-seed two-week moving-block resamples, clustering all assets by the signal calendar week. At least five matched events and four occupied matched weeks are required. Intervals are descriptive, uncorrected across horizons/subgroups and not causal proof or a profitability guarantee. Calendar, volatility and trend matching cannot remove every confounder.','',
              '### Rejected signals, final retrospective quarter','',
              'These groups include overlapping signals. Minimum-size eligibility assumes a flat portfolio at the signal close, using the recorded exchange constraints and 50 or 300 USDT. It is illustrative, not actual historical order admission. Cost and minimum groups overlap.','',
              '| Subset | Hold hours | Signals | Mean raw return | Mean net return |','|---|---:|---:|---:|---:|']
    for name in ('all','cost_rejected','cost_passed','below_minimum_50','below_minimum_300'):
        for hours,r in study['final_check']['subsets'][name].items():
            lines.append(f"| {name} | {hours} | {r['count']} | {pct(r.get('mean_gross_return'))} | {pct(r.get('mean_net_return'))} |")
    lines += ['', '## Commission headroom','',
              'For each final-quarter primary horizon, this is the symmetric fee per side that would reduce its mean return to zero after the fixed slippage assumption, before running costs. It is a retrospective arithmetic ceiling, not an achievable account fee or future forecast. A negative ceiling means the average loses even with zero commissions.','',
              '| Hold hours | Break-even commission per side |','|---|---:|']
    for hours,r in study['final_check']['subsets']['nonoverlap'].items():
        if r['count']:
            factor=(1+r['mean_gross_return'])*.999/1.001
            lines.append(f'| {hours} | {pct((factor-1)/(factor+1))} |')
    lines += ['', '## Verification and limits','',
              'Six new focused tests cover exact next-open horizons/cost arithmetic, gap and endpoint exclusion, elapsed-time signal spacing, preceding controls, disjoint control windows and sparse-cluster suppression. The full suite result is recorded in `verification.json`. Independent review prompted the disjoint-control and occupied-week checks before interpreting results; the study was restarted after those corrections.','',
              'Binance price bars are analyzed with Kraken fee/minimum assumptions. Account fees, bills, venue-specific historic execution and unseen forward outcomes remain unverified. Favorable retrospective subgroups will not be promoted without a separately fixed and evaluated strategy.','',
              'Reproduce with `scripts/audit_account_costs.py`, `scripts/run_signal_quality.py` and `scripts/report_cost_signal_audit.py` using the project Python environment. The first script reads local ledger/configuration presence and public Kraken data only. The signal script uses cached data only. Protocol, data/code hashes, full event rows and subgroup summaries are retained under `research/runs/cost-signal-audit/`.', '']
    lines += ['## Recommendation','',
              'Pause further parameter tuning of this RSI entry rule. A new, explicitly specified entry hypothesis should demonstrate useful price movement before cost/execution optimization or capital increases. Keep the current strategy unpromoted; this audit provides no evidence for doubling capital within three months.','',
              'The final-quarter cost-qualified subset contains only seven overlapping signals. Its mean 24h raw move was approximately +1.106%, but its modeled net return was approximately -0.698%. The same subset was absent in the evaluation period and had a negative 24h raw mean in development. It does not establish a robust exception.','',
              'Actual Kraken fees, AI billing rates and hosting invoices remain necessary to establish account-level profitability. Until those are known, report costs as unknown and retain sensitivity scenarios. No billed amount or account fee has been inferred from the published schedule.','']
    (ROOT/'docs/COST_AND_SIGNAL_AUDIT.md').write_text('\n'.join(lines),encoding='utf-8')
    (OUT/'manifest.json').write_text(json.dumps({str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.rglob('*')) if p.is_file() and p.name!='manifest.json'},indent=2)+'\n')
    print(ROOT/'docs/COST_AND_SIGNAL_AUDIT.md')


if __name__=='__main__':
    main()
