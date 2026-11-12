"""Fake rejected event part must not cause a second summary-only intent."""
import asyncio
import json
import unittest

import httpx

from test_paper_notifications import PaperNotificationTests, envelope, close, eventually


class FailureProbe(PaperNotificationTests):
    async def test_combined_partial_failure_keeps_one_delivery_intent(self):
        failed = []

        async def poster(url, payload):
            if 'Canonical event outcomes:' in payload['content']:
                failed.append(payload['content'])
                response = httpx.Response(500, request=httpx.Request('POST', url))
                raise httpx.HTTPStatusError('synthetic rejection', request=response.request, response=response)
            await self.poster(url, payload)

        async def no_wait(delay):
            await asyncio.sleep(0)

        reporter = self.reporter(post_json=poster, sleep=no_wait)
        await self.start(reporter)
        reporter.offer(envelope())
        await eventually(lambda: reporter._state.get('startup') is True)
        for number in (2, 3, 4):
            reporter.offer(envelope(number))
            await eventually(lambda: reporter._state.get('last_completed') == number)
        self.sent.clear()
        value = envelope(5)
        for name, row in value['accounts'].items():
            event = close('position-'+name)
            event['account'] = name
            row['closes'] = [event]
        reporter.offer(value)
        await eventually(lambda: bool(reporter._state.get('abandoned')))
        await asyncio.sleep(.7)
        groups = {}
        for _, payload in self.sent:
            tag = payload['content'].split('[paper:')[-1].split(' part:')[0]
            groups[tag] = groups.get(tag, 0)+1
        print(json.dumps(dict(confirmed_messages=len(self.sent), confirmed_groups=groups,
            failed_attempts=len(failed), abandoned=reporter._state['abandoned'],
            event_markers=len(reporter._state['events']), cycle_marker=reporter._state['cadences'].get('cycle'))))
        self.assertLessEqual(len(groups), 1,
            'Failed combined input was redispatched as a second summary-only intent')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([
        FailureProbe('test_combined_partial_failure_keeps_one_delivery_intent')]))
    raise SystemExit(not result.wasSuccessful())
