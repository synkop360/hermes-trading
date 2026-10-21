"""Render the completed staged experiment without running any simulations."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'research/runs/profitability-comparison'
LABELS = {'control_taker_15m': 'Corrected 15m control', 'maker_15m': 'Post-only maker entries',
          'hourly': '1h entries', 'hourly_with_4h_trend': '1h + 4h trend filter',
          'full_exit': 'Full RSI exit', 'delayed_trailing': 'Full RSI exit + delayed trailing',
          'fixed_target': 'Full fixed 4 ATR target', 'scalable_cap': 'Balance-based order cap',
          'scalable_full_exit_fallback': 'Balance-based cap + full-exit fallback'}


def pct(value):
    return '—' if value is None else f'{value*100:.3f}%'


def main():
    payload = json.loads((OUT/'results.json').read_text())
    results = payload['results']
    windows = ('development','evaluation','final_check')
    lines = ['# Staged profitability comparison — 2026-09-13', '',
        'No alternative qualified under the fixed development selection rule.' if not any(s['eligible'] for s in payload['selections']) else 'See the development selections and subsequent evaluation below; no candidate was promoted.', '',
        'Research only. No live strategy, running service, account setting or trading balance was changed. No paid AI training or real orders were used.', '',
        '## Fixed experiment and selection', '',
        'Development: 2025-10-01–2026-03-01. Evaluation: 2026-03-01–2026-06-01. Final retrospective check: 2026-06-01–2026-09-01. Endpoints are exclusive. Each run starts independently with fresh capital; these are not segments of a single compounded account. All periods were previously available to this project and are not certified unseen.', '',
        'The staged rule was fixed before results: at least 20 completed positions, positive net expectancy and net return, and no drawdown halt on development data. Among qualifying alternatives choose highest net return; otherwise retain the control. Retaining the control does not recommend trading it.', '']
    for stage in payload['selections']:
        lines.append(f"- {stage['stage']}: selected `{stage['selected']}`; qualifying candidates: {', '.join(stage['eligible']) or 'none'}.")
    lines += ['', '## Strategy alternatives at 300 USDT', '',
        'Returns include modeled trading fees and slippage, exclude operating costs and taxes, and mark remaining positions at estimated liquidation value. Trade counts count completed positions, combining partial legs.', '',
        '| Candidate | Development return | Evaluation return | Final-check return | Completed trades (dev / eval / final) |',
        '|---|---:|---:|---:|---:|']
    for name, label in LABELS.items():
        rows = [results[f'{name}__{window}__300'] for window in windows]
        lines.append(f"| {label} | {' | '.join(pct(r['net_return']) for r in rows)} | {' / '.join(str(r['completed_positions']) for r in rows)} |")
    base = results['control_taker_15m__development__300']
    full = results['full_exit__development__300']
    lines += ['', f"Full exits changed development win rate from {pct(base['win_rate'])} to {pct(full['win_rate'])}, while net return changed from {pct(base['net_return'])} to {pct(full['net_return'])}. A higher win rate did not establish better profitability."]
    lines += ['', '## Win rate, payoff and drawdown', '',
        'These metrics are for the final retrospective quarter at 300 USDT, including losses and fees. Missing win rate/profit factor means the denominator is unavailable, not profitability.', '',
        '| Candidate | Win rate | Average winner USDT | Average loser USDT | Profit factor | Net expectancy USDT/trade | Max drawdown | Open at end |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,label in LABELS.items():
        r = results[f'{name}__final_check__300']
        values = [r['experiment']['average_win'], r['experiment']['average_loss'], r['profit_factor'], r['expectancy']]
        formatted = ['—' if v is None else f'{float(v):.4f}' for v in values]
        lines.append(f"| {label} | {pct(r['win_rate'])} | {' | '.join(formatted)} | {pct(r['max_drawdown'])} | {r['open_positions']} |")
    control = payload['selections'][-1]['selected']
    lines += ['', '## Capital and order-cap sensitivity', '',
        'The scalable cap is min(5% of current equity, 25 USDT); the original cap is 5 USDT. Effective risk budget remains 0.125% of equity per trade, symbol exposure 5%, total exposure 10%, at most two positions. Orders below minimum are rejected, never rounded up into extra risk.', '',
        '| Candidate | Starting USDT | Development return | Evaluation return | Final-check return | Completed trades (dev / eval / final) |',
        '|---|---:|---:|---:|---:|---:|']
    for name in (control,'scalable_cap','scalable_full_exit_fallback'):
        for balance in (50,150,300,500):
            rows = [results[f'{name}__{window}__{balance}'] for window in windows]
            lines.append(f"| {LABELS[name]} | {balance} | {' | '.join(pct(r['net_return']) for r in rows)} | {' / '.join(str(r['completed_positions']) for r in rows)} |")
    lines += ['', '## Older stress period', '', '2022-01-01–2022-07-27 (end exclusive), independently starting at 300 USDT.', '', '| Candidate | Return | Trades | Drawdown |', '|---|---:|---:|---:|']
    for key,r in results.items():
        if '__older_stress__' in key:
            lines.append(f"| {LABELS[key.split('__')[0]]} | {pct(r['net_return'])} | {r['completed_positions']} | {pct(r['max_drawdown'])} |")
    lines += ['', '## Running-cost sensitivity', '',
        'Monthly 0/1/5/10 USDT costs are deducted ex post in every result JSON. This does not rerun sizing as bills reduce available funds. A 92-day quarter costs approximately 3.07 USDT at 1 USDT/month, or 15.33 USDT at 5 USDT/month. At 300 USDT those subtract approximately 1.02 and 5.11 percentage points respectively.', '',
        '## Execution assumptions and limits', '',
        '- Signals use closed bars. Market entries and discretionary exits execute at the next observed open. Stop/target checks use intrabar OHLC and prioritize stops when both are touched. This next-open discretionary-exit treatment differs from the earlier validation report.',
        '- Maker entries rest 0.1% below the signal close, rounded down to the recorded tick. Crossing opens reject; a fill requires a further 0.1% penetration during the next bar, otherwise the order expires. Admission, quantity, funds, exposure and slots are reserved before reading that bar\'s low. No same-bar target profit is credited after an intrabar maker entry. This is an OHLC execution proxy, not exchange queue/order-book replay; maker performance requires forward verification.',
        '- Tier-one modeled fees: 0.8% taker and 0.4% maker. Market slippage: 0.1% per leg. Maker fill price is the resting limit; sizing and entry screening retain a conservative two-leg slippage reserve. Actual account tier was not queried.',
        '- The 4h filter requires 101 completed, contiguous 4h bars, a rising 100-bar SMA and close above it. Hourly variants retain the 12-hour holding limit. Other inherited RSI thresholds remain fixed.',
        '- Delayed trailing starts only after a closing price reaches +1 initial stop distance, remains activated thereafter, and never removes the initial protective stop. Full RSI exits remain available. Fixed-target candidates suppress RSI exits and retain stops and the holding-time limit.',
        '- The fixed target is 4 ATR, with its own distance-versus-cost gate. Target distance is not estimated expected profit; observed net expectancy is reported separately. No fitted target-reach probability or parameter search was used.',
        '- Maker fees and fixed targets also change which entries pass the cost screen. These comparisons measure the resulting strategy, not a pure fee saving or exit change on an identical list of trades. Lower fees are not intrinsically harmful; additional qualifying trades can still lose money.',
        '- Full-exit fallback is checked both at the exit signal and at the execution open, including slippage when checking quote minimums. Protective stop precedence is retained.',
        '- Binance historical bars use the recorded Kraken minimums and fee assumptions retrospectively. The old dataset has known gaps; histories reset at gaps. Current exchange/account eligibility and historic fee changes are not modeled.',
        '- Simultaneous candidates retain deterministic asset ordering. This experiment did not fit opportunity rankings or correlation forecasts. It is a staged comparison, not an exhaustive search of interactions between execution, timeframe, exits and capital.',
        '- Research drawdown halt remains 8%, with possible gap overshoot. This is not an authorization to change stricter live limits. There is no leverage or borrowing.',
        '- Research policy options are not live-deployable strategy settings. Any promotion requires execution parity work and forward paper validation; no promotion occurred.', '',
        '## Verification and reproducibility', '',
        'All 549 tests passed after implementation. New tests cover maker penetration/crossing, resting-order slot reservation, causal higher-timeframe filtering, persistent delayed trailing, next-open exits with gap-stop precedence, implicit RSI suppression, execution-time partial fallback and halt precedence after missing bars. Independent review findings were corrected and re-reviewed.', '',
        'After the historical runs, one guard was corrected so a previously triggered drawdown halt overrides a queued partial sale after a missing bar. The exact run source is preserved in `code_snapshot/`. `verification.json` verifies that the only subsequent research-code difference is this guard and that none of the 57 runs ever halted (the flag only changes from false to true). Therefore the changed branch cannot affect the recorded runs. The new halt regression test passed; the historical simulations were not repeated after this guard-only correction. A future rerun on current code will correctly invalidate the old code fingerprints.', '',
        'Run ` .venv/Scripts/python.exe scripts/run_profitability_comparison.py ` from the repository to reproduce; matching fingerprints reuse results. Run ` .venv/Scripts/python.exe scripts/report_profitability_comparison.py ` to regenerate this report. No credentials are needed.', '',
        'Artifacts: `research/runs/profitability-comparison/results.json`, `protocol.json`, `selections.json`, frozen `candidates/` and individual result files. Each result fingerprints candidate settings, code, runner, source data, dates, warmup, capital and drawdown limit. The original return-validation artifacts are preserved.', '']
    lines += ['## Decision', '',
        'No candidate is promoted. The tested changes do not establish sustained profitability or support doubling the starting balance within three months. Increasing capital and order caps enabled additional trades but did not establish a profitable sizing threshold. The positive 150 USDT final-quarter control result was approximately 0.10 USDT on three completed trades, before operating costs, following a negative development period.', '',
        'Keep these changes as research capabilities. Further work would need a separately specified strategy hypothesis and forward evaluation; widening risk limits or adding capital is not justified by this comparison.', '']
    target = ROOT/'docs/PROFITABILITY_COMPARISON_RESULTS.md'
    target.write_text('\n'.join(lines), encoding='utf-8')
    files = sorted(p for p in OUT.rglob('*') if p.is_file() and p.name != 'manifest.json')
    (OUT/'manifest.json').write_text(json.dumps({str(p.relative_to(OUT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2)+'\n')
    print(target)


if __name__ == '__main__':
    main()
