"""Invented public venue records for correctness checks; never market evidence."""
from hermes_trading.venue_constraints import constraint_record


def venue_record(asset, at, *, minimum_quantity=None, minimum_quote='0.45', decimals=8):
    alt, ws, base, minimum = {
        'BTC/EUR': ('XBTEUR','XBT/EUR','XXBT','0.00006'),
        'ETH/EUR': ('ETHEUR','ETH/EUR','XETH','0.002'),
        'XRP/EUR': ('XRPEUR','XRP/EUR','XXRP','4'),
    }[asset]
    pair={'altname':alt,'wsname':ws,'base':base,'quote':'ZEUR','aclass_base':'currency',
          'aclass_quote':'currency','lot':'unit','lot_multiplier':1,'lot_decimals':decimals,
          'ordermin':minimum_quantity or minimum,'costmin':minimum_quote,'status':'online',
          'fees':[],'fees_maker':[]}
    return constraint_record(pair,asset=asset,received_at=at,
        source_url='https://api.kraken.com/0/public/AssetPairs?pair='+alt+'&country_code=FR')
