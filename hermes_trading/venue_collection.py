"""One bounded, anonymous public spot-metadata request per common input."""
import asyncio
from datetime import datetime,timezone
import json
from urllib.parse import urlencode
import httpx

from hermes_trading.venue_constraints import constraint_record,VenueConstraintsError


class PublicVenueClient:
    def __init__(self, *, transport=None, clock=None, timeout_seconds=20):
        if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float)) or not 0<timeout_seconds<=20:
            raise ValueError('venue request timeout must be from zero through 20 seconds')
        self.timeout_seconds=timeout_seconds
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.client=httpx.AsyncClient(transport=transport,timeout=timeout_seconds,
                                    follow_redirects=False,trust_env=False)
        self.closed=False

    async def __aenter__(self):return self

    async def __aexit__(self,*args):
        await self.client.aclose();self.closed=True

    async def fetch_many(self, assets, *, country_code='FR'):
        names={'BTC/EUR':'XBTEUR','ETH/EUR':'ETHEUR','XRP/EUR':'XRPEUR'}
        assets=list(assets)
        if country_code!='FR' or not assets or len(assets)>3 or len(set(assets))!=len(assets) or any(asset not in names for asset in assets):
            raise VenueConstraintsError('unsupported public venue request scope')
        failure={asset:{'error':'venue_metadata_unavailable'} for asset in assets}
        url='https://api.kraken.com/0/public/AssetPairs?'+urlencode({'pair':','.join(names[asset] for asset in assets),'country_code':country_code})
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self.client.stream('GET',url) as response:
                    if response.status_code!=200:raise ValueError('public venue request failed')
                    raw=bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw)+len(chunk)>65536:raise ValueError('public venue response exceeds 64 KiB')
                        raw.extend(chunk)
                    received=self.clock()
            data=json.loads(raw)
            if not isinstance(data,dict) or data.get('error')!=[] or not isinstance(data.get('result'),dict):
                raise ValueError('invalid public venue envelope')
        except (httpx.HTTPError,TimeoutError,ValueError,TypeError,UnicodeError):return failure
        result={}
        for asset in assets:
            pairs=[pair for pair in data['result'].values() if isinstance(pair,dict) and pair.get('altname')==names[asset]]
            try:
                if len(pairs)!=1:raise VenueConstraintsError('missing or duplicate public pair')
                result[asset]=constraint_record(pairs[0],asset=asset,received_at=received,source_url=url,country_code=country_code)
            except (VenueConstraintsError,TypeError):result[asset]=failure[asset]
        return result
