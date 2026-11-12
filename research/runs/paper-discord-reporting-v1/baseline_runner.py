from pathlib import Path
import sys,unittest
from unittest.mock import patch
import httpx
sys.path.insert(0,str(Path('tests').resolve()))
suite=unittest.TestLoader().loadTestsFromNames(['test_notifications','test_paper_boot','test_paired_worker'])
async def deny(*a,**k): raise AssertionError('real HTTP forbidden in synthetic verification')
def deny_sync(*a,**k): raise AssertionError('real HTTP forbidden in synthetic verification')
with patch.object(httpx.AsyncHTTPTransport,'handle_async_request',deny),patch.object(httpx.HTTPTransport,'handle_request',deny_sync):
 result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
