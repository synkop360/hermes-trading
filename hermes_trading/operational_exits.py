"""Operational exits remain in cash/P&L but cannot qualify strategy evidence."""

PAIRED_OPERATIONAL_STOP_REASONS = frozenset({'paired_storage_stop', 'paired_archive_stop'})
RISK_STOP_REASONS = PAIRED_OPERATIONAL_STOP_REASONS | {'paired_drawdown_stop'}
RECOVERY_EXIT_REASONS = RISK_STOP_REASONS | {'entry_contract_recovery_exit'}
