"""Dated public spot limits; these facts confer no private trading permission."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit


class VenueConstraintsError(ValueError):
    pass


_FIELDS = {'schema_version','purpose','exchange','asset','quote_currency','country_code',
           'received_at','source_url','source_sha256','source_pair'}
_SYMBOLS = {'BTC/EUR': ('XBTEUR','XBT/EUR','XXBT'),
            'ETH/EUR': ('ETHEUR','ETH/EUR','XETH'),
            'XRP/EUR': ('XRPEUR','XRP/EUR','XXRP')}


def _json(value):
    try:
        encoded = json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,
                             allow_nan=False).encode('utf-8')
    except (ValueError,TypeError,RecursionError) as error:
        raise VenueConstraintsError('venue facts must be bounded JSON') from error
    if len(encoded)>8192:raise VenueConstraintsError('venue record exceeds 8 KiB')
    return encoded


def _time(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value,str) else value
        if not isinstance(parsed,datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('timezone required')
        return parsed.astimezone(timezone.utc)
    except (TypeError,ValueError,OverflowError) as error:
        raise VenueConstraintsError('invalid aware venue timestamp') from error


def _minimum(value):
    if not isinstance(value,str) or len(value)>64 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',value):
        raise VenueConstraintsError('venue minima must be positive decimal strings')
    number=Decimal(value)
    if not number.is_finite() or number<=0 or number.adjusted()>30 or number.as_tuple().exponent < -36:
        raise VenueConstraintsError('venue minimum outside supported decimal range')
    return number


def _number(value):
    if not isinstance(value,Decimal) or not value.is_finite() or value<=0:
        raise VenueConstraintsError('order quantity and price must be positive finite Decimal values')
    if value.adjusted()>30 or value.as_tuple().exponent < -36:
        raise VenueConstraintsError('order value outside supported decimal range')
    return value


@dataclass(frozen=True)
class VenueConstraints:
    asset: str
    minimum_quantity: Decimal
    minimum_quote: Decimal
    quantity_decimals: int
    received_at: datetime
    source_sha256: str

    def quantize_entry(self, quantity: Decimal) -> Decimal:
        _number(quantity)
        with localcontext() as context:
            context.prec=100
            return quantity.quantize(Decimal(1).scaleb(-self.quantity_decimals),rounding=ROUND_DOWN)

    def validate_order(self, quantity: Decimal, price: Decimal) -> None:
        _number(quantity);_number(price)
        if self.quantize_entry(quantity)!=quantity:
            raise VenueConstraintsError('quantity exceeds current venue precision')
        with localcontext() as context:
            context.prec=100
            if quantity<self.minimum_quantity or quantity*price<self.minimum_quote:
                raise VenueConstraintsError('order below current venue minimum')


def constraint_record(pair, *, asset, received_at, source_url, country_code='FR') -> dict:
    # A JSON copy prevents the collector's mutable response from changing a
    # previously frozen input. This digest is of canonical pair JSON, not wire bytes.
    source=json.loads(_json(pair))
    record={'schema_version':1,'purpose':'paper_executable_venue_v1','exchange':'kraken',
            'asset':asset,'quote_currency':'EUR','country_code':country_code,
            'received_at':_time(received_at).isoformat(),'source_url':source_url,
            'source_sha256':hashlib.sha256(_json(source)).hexdigest(),'source_pair':source}
    parse_venue_constraints(record,asset=asset,at=received_at,country_code=country_code)
    return record


def parse_venue_constraints(record, *, asset, at, quote_currency='EUR', country_code='FR',
                            max_age_seconds=300) -> VenueConstraints:
    if not isinstance(record,Mapping) or set(record)!=_FIELDS:
        raise VenueConstraintsError('missing or unsupported venue record fields')
    _json(record)
    if type(max_age_seconds) is not int or not 0<max_age_seconds<=300:
        raise VenueConstraintsError('venue freshness must be from 1 through 300 seconds')
    if asset not in _SYMBOLS or quote_currency!='EUR' or country_code!='FR':
        raise VenueConstraintsError('unsupported venue scope')
    if (type(record['schema_version']) is not int or record['schema_version']!=1 or
        record['purpose']!='paper_executable_venue_v1' or record['exchange']!='kraken' or
        record['asset']!=asset or record['quote_currency']!=quote_currency or record['country_code']!=country_code):
        raise VenueConstraintsError('venue record purpose or scope mismatch')
    received=_time(record['received_at']);now=_time(at)
    age=(now-received).total_seconds()
    if not 0<=age<max_age_seconds:raise VenueConstraintsError('venue facts are stale or future dated')
    pair=record['source_pair']
    if not isinstance(pair,dict) or record['source_sha256']!=hashlib.sha256(_json(pair)).hexdigest():
        raise VenueConstraintsError('venue pair digest mismatch')
    altname,wsname,base=_SYMBOLS[asset]
    try:
        source=urlsplit(record['source_url'])
        query=parse_qs(source.query,keep_blank_values=True,strict_parsing=True)
        if (source.scheme!='https' or source.netloc!='api.kraken.com' or source.path!='/0/public/AssetPairs' or
            source.fragment or set(query)-{'pair','country_code','assetVersion'} or
            query.get('country_code')!=[country_code] or len(query.get('pair',[]))!=1 or
            altname not in query['pair'][0].split(',') or
            (query.get('assetVersion') is not None and query['assetVersion']!=['1'])):
            raise ValueError('invalid public pair scope')
    except (TypeError,ValueError,AttributeError) as error:
        raise VenueConstraintsError('invalid public venue source URL') from error
    if (pair.get('altname')!=altname or pair.get('wsname')!=wsname or pair.get('base')!=base or
        pair.get('quote')!='ZEUR' or pair.get('aclass_base')!='currency' or pair.get('aclass_quote')!='currency' or
        pair.get('lot')!='unit' or type(pair.get('lot_multiplier')) is not int or pair['lot_multiplier']!=1 or
        pair.get('status')!='online'):
        raise VenueConstraintsError('pair is not a supported online EUR spot market')
    precision=pair.get('lot_decimals')
    if type(precision) is not int or not 0<=precision<=18:
        raise VenueConstraintsError('invalid venue quantity precision')
    return VenueConstraints(asset,_minimum(pair.get('ordermin')),_minimum(pair.get('costmin')),
                            precision,received,record['source_sha256'])
