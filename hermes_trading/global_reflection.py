"""One durable global LLM review; changed candidates await publication.

This coordinator never writes the active strategy or calls asset-level fallback
generators. The existing watcher must be routed here in the controlled release.
"""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import yaml

from hermes_trading.execution_policy import strategy_is_frozen
from hermes_trading.model_runner import ModelResponseCaptureError, ModelDispatchDeferred, ReflectionModelRunner
from hermes_trading.reflect import (
    _next_version, STRATEGY_VARIABLE_BOUNDS, STRATEGY_VARIABLE_MAX_STEP,
)
from hermes_trading.paper_strategy_proposal import apply_paper_strategy_proposal
from hermes_trading.reflection_supervision import (
    ReviewLease, _json, _now, _time, _hash, assert_writer_owner,
)


class _ReviewCostLedger:
    def __init__(self,ledger,context):self.ledger=ledger;self.context=context
    def record(self,event):
        self.ledger.record(replace(event,metadata={**event.metadata,**self.context}))


def _recent_trades(rows, now):
    found = {}
    for row in rows:
        if row.get("status", "closed") != "closed":
            continue
        try:
            closed = _time(row.get("closed_at") or row.get("timestamp"))
        except (TypeError, ValueError):
            continue
        if closed > now:
            continue
        identity = row.get("position_id") or row.get("trade_id") or row.get("id")
        asset = row.get("asset")
        if not isinstance(identity, str) or not identity or not isinstance(asset, str) or not asset:
            continue
        key = _json([asset, identity])
        if key in found and _json(found[key][1]) != _json(row):
            raise ValueError("conflicting trade contents in recent history")
        found[key] = (closed, row)
    return [value[1] for _, value in sorted(found.items(), key=lambda item: (item[1][0], item[0]))][-25:]


def run_global_review(state_dir, runner, *, now=None):
    state_dir, current = Path(state_dir), _now(now)
    from hermes_trading.paper_publication import assert_no_pending_paired_batch
    assert_no_pending_paired_batch(state_dir)
    policy = assert_writer_owner(state_dir, "external_llm")
    if policy is None:
        raise ValueError("global review requires explicit ownership")
    candidate = getattr(runner, "pinned_candidate", None)
    if not isinstance(runner, ReflectionModelRunner) or runner.strict_pinned is not True or candidate is None:
        raise ValueError("global review requires strict pinned execution")
    if (candidate.provider, candidate.model) != (policy["model"]["provider"], policy["model"]["name"]):
        raise ValueError("runner does not match the experiment's pinned model")
    if candidate.free is not True and runner.paid_calls_authorized is not True:
        raise PermissionError("paid model review has not been authorized")

    with ReviewLease(state_dir) as lease:
        journal = state_dir / "trades.jsonl"
        contents = journal.read_text(encoding="utf-8") if journal.exists() else ""
        rows = [json.loads(line) for line in contents.splitlines() if line.strip()]
        due = lease.due(rows, now=current)
        if not due["eligible"]:
            return {"status": "not_due", "reason": due["reason"]}
        strategy_bytes = (state_dir / "strategy.yaml").read_bytes()
        goal_bytes = (state_dir / "goal.yaml").read_bytes()
        strategy, goal = yaml.safe_load(strategy_bytes), yaml.safe_load(goal_bytes)
        if not isinstance(strategy, dict) or not isinstance(goal, dict):
            raise ValueError("strategy and goal must be objects")
        if goal.get("one_variable_only", True) is not True:
            raise ValueError("global review requires the one-variable goal")
        if strategy_is_frozen(strategy):
            return {"status": "not_due", "reason": "strategy_frozen"}
        request = {"strategy": strategy, "goal": goal, "recent_trades": _recent_trades(rows, current),
                   "strategy_sha256": hashlib.sha256(strategy_bytes).hexdigest(),
                   "goal_sha256": hashlib.sha256(goal_bytes).hexdigest(), "observed_at": current.isoformat()}
        account_path=state_dir/'wallet_metrics.json'
        if account_path.exists() and account_path.read_bytes():
            account=json.loads(account_path.read_text(encoding='utf-8'))
            if not isinstance(account,dict) or account.get('status')!='observed' or account.get('experiment_id')!=policy['experiment_id']:
                raise ValueError('wallet inputs do not describe the current experiment')
            if not 0<=(current-_time(account.get('observed_at'))).total_seconds()<=600:
                raise ValueError('wallet inputs are stale or future-dated')
            request['wallet_account']=account
        else:
            if (state_dir/'wallet_account.json').exists() or policy.get('wallet_required') is True:
                raise ValueError('configured wallet report is unavailable')
            request['wallet_account']={'status':'not_configured'}
        prompt = (
            'Review the whole strategy once. Return one JSON object: '
            '{"outcome":"no_change","reason":"..."} or '
            '{"outcome":"change","asset":"configured symbol or null for global",'
            '"variable":"one allowed variable","value":number,"reason":"..."}. '
            'Never return a changes list. Keep risk limits and fees unchanged. '
            'A global change must affect an active asset after symbol overrides are applied. '
            'Use a configured active asset to change its override; otherwise prefer no_change. '
            'position_size_r is a locked risk setting and cannot be changed. '
            'Trade P&L is not a rolling wallet return; unknown costs are not zero. '
            'The supplied trades may span entry versions. Prefer no_change when evidence is inadequate. '
            'Use wallet_account for wallet returns/drawdown; missing costs and incomplete history are unqualified. '
            + _json({"variable_bounds": STRATEGY_VARIABLE_BOUNDS,
                     "maximum_steps": STRATEGY_VARIABLE_MAX_STEP, "inputs": request})
        )
        request["prompt"] = prompt
        review = lease.prepare(rows, now=current)
        review_id = review["review_id"]
        lease.record_material(review_id, request=request, now=current)
        original_ledger=runner.cost_ledger
        if original_ledger is not None:
            runner.cost_ledger=_ReviewCostLedger(original_ledger,{'experiment_id':policy['experiment_id'],
                'policy_hash':_hash(policy),'review_id':review_id,'request_started_at':review['started_at']})
        try:
            result = runner.run(prompt, now=current, response_capture=lambda response:
                                lease.record_material(review_id, response=response, now=_now(now)))
        except ModelDispatchDeferred as error:
            lease.defer_before_dispatch(review_id,detail=str(error),now=_now(now))
            return {"status":"deferred","reason":"invocation_budget","review_id":review_id}
        except ModelResponseCaptureError:
            # The request's outcome is uncertain. Keep its durable pending ID.
            raise
        except Exception as error:
            lease.fail(review_id, reason=str(error), now=_now(now))
            return {"status": "failed", "review_id": review_id, "error": str(error)}
        finally:
            runner.cost_ledger=original_ledger
        finished = _now(now)
        if (result.provider, result.model) != (candidate.provider, candidate.model):
            lease.fail(review_id, reason="model response provenance differs from the pin", now=finished)
            return {"status": "failed", "review_id": review_id, "error": "model provenance mismatch"}
        if ((state_dir / "strategy.yaml").read_bytes() != strategy_bytes
                or (state_dir / "goal.yaml").read_bytes() != goal_bytes):
            lease.record_material(review_id, decision={"status": "awaiting_reconciliation",
                                                      "reason": "strategy or goal changed during review"}, now=finished)
            return {"status": "awaiting_reconciliation", "review_id": review_id}
        try:
            proposal = json.loads(result.output)
            if not isinstance(proposal, dict) or "changes" in proposal:
                raise ValueError("exactly one global decision is required")
            if not isinstance(proposal.get("reason"), str) or not proposal["reason"].strip():
                raise ValueError("a nonempty review reason is required")
            if proposal.get("outcome") == "no_change":
                if set(proposal) != {"outcome", "reason"}:
                    raise ValueError("no_change cannot contain a strategy modification")
                decision = {"status": "no_change", "reason": proposal.get("reason", "")}
            elif proposal.get("outcome") == "change":
                if set(proposal) - {"outcome", "reason", "variable", "value", "asset"}:
                    raise ValueError("unexpected change fields")
                if not isinstance(proposal.get("variable"), str) or type(proposal.get("value")) not in {int, float}:
                    raise ValueError("one named variable and numeric value are required")
                if proposal["variable"] not in STRATEGY_VARIABLE_BOUNDS:
                    raise ValueError("variable must use an exact canonical name")
                if proposal.get("asset") is not None and not isinstance(proposal["asset"], str):
                    raise ValueError("asset must identify a configured symbol")
                if proposal.get("variable") == "position_size_r":
                    raise ValueError("position_size_r is a locked risk setting")
                assets = goal.get('assets', [goal['asset']] if goal.get('asset') else None)
                updated, variable, asset = apply_paper_strategy_proposal(strategy, proposal, assets=assets)
                updated["version"] = _next_version(str(strategy.get("version", "01")))
                decision = {"status": "awaiting_publication", "candidate": updated,
                            "variable": variable, "asset": asset, "reason": proposal.get("reason", ""),
                            "based_on_sha256": request["strategy_sha256"]}
            else:
                raise ValueError("explicit change or no_change outcome is required")
        except (ValueError, TypeError, KeyError) as error:
            decision = {"status": "rejected", "reason": str(error)}
        lease.record_material(review_id, decision=decision, now=finished)
        if decision["status"] in {"no_change", "rejected"}:
            lease.complete(review_id, outcome=decision["status"], provider=result.provider, model=result.model, now=finished)
        return {"status": decision["status"], "review_id": review_id}
