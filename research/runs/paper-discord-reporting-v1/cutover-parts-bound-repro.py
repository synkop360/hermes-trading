"""One native-shape input causes two delivery groups; fake HTTP only."""
import json
import unittest

from test_paper_notifications import PaperNotificationTests, envelope, close, eventually


class BoundProbe(PaperNotificationTests):
    async def test_one_captured_envelope_total_parts(self):
        reporter = self.reporter()
        await self.start(reporter)
        reporter.offer(envelope())
        await eventually(lambda: reporter._state.get('startup') is True)
        for number in (2, 3, 4):
            reporter.offer(envelope(number))
            await eventually(lambda: reporter._state.get('last_completed') == number)
        self.sent.clear()
        value = envelope(5)
        for name, row in value['accounts'].items():
            event = close('native-risk-close-'+name)
            event['account'] = name
            row['closes'] = [event]
        reporter.offer(value)
        await eventually(lambda: reporter._state['cadences'].get('cycle') == '1')
        groups = {}
        for _, payload in self.sent:
            tag = payload['content'].split('[paper:')[-1].split(' part:')[0]
            groups[tag] = groups.get(tag, 0)+1
        print(json.dumps(dict(one_offered_envelope_parts=len(self.sent),
            delivery_groups=groups, offered_close_events=8, cycle_summary_due=True)))
        self.assertLessEqual(len(self.sent), 4,
            'One v1 offered envelope exceeds approved four-part total')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        BoundProbe('test_one_captured_envelope_total_parts')]))
    raise SystemExit(not result.wasSuccessful())
