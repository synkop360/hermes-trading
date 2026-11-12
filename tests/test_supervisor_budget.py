import math
import unittest
from unittest.mock import patch

from hermes_trading.supervisor_budget import InvocationBudget, InvocationBudgetExpired, active_budget


class InvocationBudgetTests(unittest.TestCase):
    def test_elapsed_time_and_reserved_tail_cap_transport(self):
        clock=[100.0]
        budget=InvocationBudget(200.0,clock=lambda:clock[0])
        self.assertEqual(budget.timeout(60,reserve=40),60)
        clock[0]=150.0
        self.assertEqual(budget.timeout(60,reserve=40),10)
        self.assertFalse(budget.can_fit(60,reserve=40))
        clock[0]=160.0
        with self.assertRaises(InvocationBudgetExpired):budget.timeout(30,reserve=40)
        self.assertEqual(budget.timeout(30,reserve=5),30)

    def test_context_restores_previous_budget_and_reserve_after_error(self):
        one=InvocationBudget(200,clock=lambda:100)
        two=InvocationBudget(150,clock=lambda:100)
        self.assertIsNone(active_budget())
        with one.activate():
            self.assertIs(active_budget(),one)
            with self.assertRaises(RuntimeError),one.phase(reserve=40):
                self.assertEqual(one.timeout(80),60)
                with two.activate():self.assertIs(active_budget(),two)
                raise RuntimeError('test')
            self.assertEqual(one.timeout(80),80)
        self.assertIsNone(active_budget())

    def test_bad_or_regressing_clock_fails_closed(self):
        for bad in (True,math.nan,math.inf,0,99,341):
            with self.subTest(deadline=bad),self.assertRaises(ValueError):InvocationBudget(bad,clock=lambda:100)
        clock=[100.0];budget=InvocationBudget(200,clock=lambda:clock[0])
        clock[0]=99
        with self.assertRaises(ValueError):budget.timeout(30)

    def test_real_transport_receives_remaining_timeout(self):
        from scripts import hermes_watch
        budget=InvocationBudget(200,clock=lambda:150)
        with budget.activate(),budget.phase(reserve=40),patch.object(hermes_watch.subprocess,'run') as run:
            hermes_watch._run(['inert'],timeout_seconds=30)
        self.assertEqual(run.call_args.kwargs['timeout'],10)
        with budget.activate(),budget.phase(reserve=50),patch.object(hermes_watch.subprocess,'run') as run:
            with self.assertRaises(InvocationBudgetExpired):hermes_watch._run(['inert'],timeout_seconds=30)
            run.assert_not_called()
