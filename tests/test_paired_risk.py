from copy import deepcopy
from datetime import datetime,timezone
import unittest
from hermes_trading.paired_risk import initial_risk_state,update_risk_state,is_halted

NOW=datetime(2026,10,10,tzinfo=timezone.utc)
class PairedRiskTests(unittest.TestCase):
    def values(self):return {f'{arm}_{wallet}':str(wallet) for arm in ('adaptive','control') for wallet in (50,100,150,200)}
    def test_exact_threshold_stops_pair_and_recovery_never_resumes(self):
        state=initial_risk_state();values=self.values();values['adaptive_50']='48.5'
        state=update_risk_state(state,values,at=NOW)
        self.assertTrue(is_halted(state,'adaptive',50));self.assertTrue(is_halted(state,'control',50))
        self.assertFalse(is_halted(state,'adaptive',100))
        values['adaptive_50']='60';state=update_risk_state(state,values,at=NOW)
        self.assertTrue(is_halted(state,'adaptive',50))
    def test_running_peak_primary_global_and_unknown_never_reset_state(self):
        state=initial_risk_state();values=self.values();values['control_100']='110'
        state=update_risk_state(state,values,at=NOW)
        values['control_100']='106.7';state=update_risk_state(state,values,at=NOW)
        self.assertTrue(state['global_halted'])
        self.assertTrue(all(is_halted(state,arm,wallet) for arm in ('adaptive','control') for wallet in (50,100,150,200)))
        values['control_100']=None;state=update_risk_state(state,values,at=NOW)
        self.assertEqual(state['accounts']['control_100']['peak'],'110')
        self.assertTrue(state['global_halted'])
    def test_no_false_breach_below_boundary_and_nonfinite_rejected(self):
        state=initial_risk_state();values=self.values();values['adaptive_50']='48.5001'
        state=update_risk_state(state,values,at=NOW);self.assertFalse(is_halted(state,'adaptive',50))
        values['adaptive_50']='NaN'
        with self.assertRaises(ValueError):update_risk_state(state,values,at=NOW)

if __name__=='__main__':unittest.main()
