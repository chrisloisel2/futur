import json, time
from market_physics_v3.collectors.normalize import BookDeltaState, parse_binance, parse_bybit, parse_okx, parse_hyperliquid
from market_physics_v3.collectors.specs import subscriptions
from market_physics_v3.collectors.writer import AppendOnlyEventWriter
from market_physics_v3.schema import BookEvent, TradeEvent, DerivativeEvent
R=2_000_000_000_000_000_000

def test_binance_depth_trade_liq_mark():
 s=BookDeltaState();
 b=parse_binance({'e':'depthUpdate','E':1000,'s':'BTCUSDT','u':2,'b':[['100','2']],'a':[['101','3']]},R,s)
 assert len(b)==2 and b[0].event_type=='update'
 t=parse_binance({'e':'aggTrade','E':1001,'T':1001,'s':'BTCUSDT','a':9,'p':'100','q':'2','m':True},R,s)[0]; assert t.aggressor=='sell'
 l=parse_binance({'e':'forceOrder','E':1002,'o':{'T':1002,'s':'BTCUSDT','S':'SELL','q':'2','p':'99','ap':'98'}},R,s)[0]; assert l.side=='long' and l.value==196
 m=parse_binance({'e':'markPriceUpdate','E':1003,'s':'BTCUSDT','p':'100','i':'99','r':'0.0001'},R,s); assert {x.kind for x in m}=={'mark','index','funding'}

def test_bybit_all_streams():
 s=BookDeltaState(); msg={'topic':'orderbook.50.BTCUSDT','type':'snapshot','ts':1000,'data':{'s':'BTCUSDT','b':[['100','1']],'a':[['101','2']],'u':1,'seq':2,'cts':999}}
 assert parse_bybit(msg,R,s)[0].event_type=='snapshot'
 tr=parse_bybit({'topic':'publicTrade.BTCUSDT','data':[{'T':1001,'s':'BTCUSDT','S':'Buy','v':'2','p':'100','i':'x'}]},R,s)[0]; assert tr.aggressor=='buy'
 liq=parse_bybit({'topic':'allLiquidation.BTCUSDT','data':[{'T':1002,'s':'BTCUSDT','S':'Buy','v':'2','p':'90'}]},R,s)[0]; assert liq.side=='long'
 tick=parse_bybit({'topic':'tickers.BTCUSDT','ts':1003,'data':{'symbol':'BTCUSDT','openInterest':'10','fundingRate':'0.01','markPrice':'100','indexPrice':'99'}},R,s); assert len(tick)==4

def test_okx_and_hyperliquid():
 s=BookDeltaState(); o=parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'snapshot','data':[{'ts':'1000','seqId':3,'bids':[['100','1','0','1']],'asks':[['101','1','0','1']]}]},R,s); assert len(o)==2
 tr=parse_okx({'arg':{'channel':'trades','instId':'BTC-USDT-SWAP'},'data':[{'ts':'1001','tradeId':'1','px':'100','sz':'2','side':'sell'}]},R,s)[0]; assert tr.symbol=='BTCUSDT' and tr.aggressor=='sell'
 h=parse_hyperliquid({'channel':'l2Book','data':{'coin':'BTC','time':1002,'levels':[[{'px':'100','sz':'2','n':1}],[{'px':'101','sz':'3','n':1}]]}},R,s); assert len(h)==2 and h[0].symbol=='BTCUSDT'
 ht=parse_hyperliquid({'channel':'trades','data':[{'coin':'BTC','time':1003,'side':'B','px':'100','sz':'1','tid':7}]},R,s)[0]; assert ht.aggressor=='buy'
 hc=parse_hyperliquid({'channel':'activeAssetCtx','data':{'coin':'BTC','ctx':{'funding':'0.001','openInterest':'10','markPx':'100','oraclePx':'99'}}},R,s); assert {x.kind for x in hc}=={'funding','open_interest','mark','index'}

def test_subscriptions_official_shapes():
 assert 'btcusdt@depth@100ms' in subscriptions('binance',['BTCUSDT'])['subscribe']['params']
 assert 'orderbook.50.BTCUSDT' in subscriptions('bybit',['BTCUSDT'])['subscribe']['args']
 assert subscriptions('okx',['BTCUSDT'])['subscribe']['args'][0]['instId']=='BTC-USDT-SWAP'
 assert len(subscriptions('hyperliquid',['BTCUSDT'])['subscribe_many'])==4

def test_append_only_writer(tmp_path):
 from market_physics_v3.schema import TradeEvent
 e=TradeEvent('binance','BTCUSDT',1_700_000_000_000_000_000,1_700_000_000_000_000_001,'1',100,1,'buy')
 w=AppendOnlyEventWriter(tmp_path/'market_physics_v3'); p=w.append(e); w.append(e); w.close()
 assert len(p.read_text().strip().splitlines())==2

def test_stablecoin_asof_is_strict_t1():
 import pandas as pd
 from market_physics_v3.external import stablecoin_state_asof
 table=pd.DataFrame({
   'date':pd.to_datetime(['2026-01-01','2026-01-02'],utc=True),
   'research_available_at':pd.to_datetime(['2026-01-02','2026-01-03'],utc=True),
   'trio':[16.0,17.0],
   'source_quality':['PIT_AGGREGATED_T1','PIT_AGGREGATED_T1']
 })
 assert stablecoin_state_asof(table,'2026-01-01T23:59:59Z')=={}
 out=stablecoin_state_asof(table,'2026-01-02T00:00:00Z')
 assert out['stablecoin__trio']==16.0

def test_coverage_accepts_pit_for_slow_data_not_for_l2():
 from market_physics_v3.coverage import audit_feed_status
 base={
  'l2_book_events':'EVENT_LEVEL','tick_trades':'EVENT_LEVEL','bbo':'EVENT_LEVEL',
  'binance':'EVENT_LEVEL','bybit':'EVENT_LEVEL','okx':'EVENT_LEVEL','hyperliquid':'EVENT_LEVEL',
  'open_interest':'PIT_AGGREGATED','funding':'PIT_AGGREGATED','mark_index_premium':'EVENT_LEVEL','liquidations':'EVENT_LEVEL',
  'option_quotes':'EVENT_LEVEL','option_trades':'EVENT_LEVEL','option_open_interest':'PIT_AGGREGATED',
  'decision_send_ack_fill':'EVENT_LEVEL','future_markouts':'EVENT_LEVEL',
  'stablecoin_flows':'PIT_AGGREGATED','etf_cme':'PIT_AGGREGATED','macro_events':'PIT_AGGREGATED','news_events':'PIT_AGGREGATED'}
 r=audit_feed_status(base)
 assert r['ready_for_full_market_physics_research']
 base['l2_book_events']='PIT_AGGREGATED'
 r=audit_feed_status(base)
 assert not r['ready_for_p0_market_research'] and 'l2_book_events' in r['families']['microstructure']['blocking']


def test_l2_zero_qty_is_remove_not_true_cancel():
 from market_physics_v3.collectors.normalize import BookDeltaState, parse_binance
 s=BookDeltaState(); r=2_000_000_000_000_000_000
 parse_binance({'e':'depthUpdate','E':1000,'s':'BTCUSDT','u':1,'b':[['100','2']],'a':[]},r,s)
 x=parse_binance({'e':'depthUpdate','E':1001,'s':'BTCUSDT','u':2,'b':[['100','0']],'a':[]},r,s)[0]
 assert x.event_type=='remove'

def test_sequence_gap_is_fail_closed():
 import pytest
 from market_physics_v3.collectors.normalize import BookDeltaState, parse_okx, SequenceGap
 s=BookDeltaState(); r=2_000_000_000_000_000_000
 parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'snapshot','data':[{'ts':'1000','seqId':10,'bids':[['100','1','0','1']],'asks':[['101','1','0','1']]}]},r,s)
 with pytest.raises(SequenceGap):
  parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'update','data':[{'ts':'1001','prevSeqId':9,'seqId':11,'bids':[['100','2','0','1']],'asks':[]}]},r,s)

def test_buffered_writer_flushes_on_close(tmp_path):
 from market_physics_v3.collectors.writer import AppendOnlyEventWriter
 from market_physics_v3.schema import TradeEvent
 e=TradeEvent('binance','BTCUSDT',1_700_000_000_000_000_000,1_700_000_000_000_000_001,'1',100,1,'buy')
 w=AppendOnlyEventWriter(tmp_path/'market_physics_v3',flush_every=999999,flush_interval_s=999999)
 p=w.append(e)
 w.close()
 assert len(p.read_text().strip().splitlines())==1

def test_binance_book_ticker_is_bbo_snapshot():
 s=BookDeltaState()
 x=parse_binance({'e':'bookTicker','E':1000,'s':'BTCUSDT','u':9,'b':'100','B':'2','a':'101','A':'3'},R,s)
 assert len(x)==2 and all(e.event_type=='snapshot' for e in x)

def test_remove_and_cancel_are_distinct_physical_features():
 from market_physics_v3.microstructure import removal_imbalance,cancellation_imbalance
 e=BookEvent('x','BTCUSDT',1000,1000,1,'remove','ask',101,5)
 assert removal_imbalance([e])==1.0
 assert cancellation_imbalance([e])==0.0


def test_okx_liquidation_detail_timestamp_and_position_side():
 s=BookDeltaState(); r=2_000_000_000_000_000_000
 out=parse_okx({'arg':{'channel':'liquidation-orders','instType':'SWAP'},'data':[{'instId':'BTC-USDT-SWAP','details':[{'ts':'1005','bkPx':'90','sz':'2','posSide':'long','side':'sell'}]}]},r,s)
 assert len(out)==1
 assert out[0].side=='long' and out[0].value==180.0 and out[0].symbol=='BTCUSDT'


def test_binance_bbo_snapshot_does_not_clear_deeper_delta_state():
 s=BookDeltaState(); r=2_000_000_000_000_000_000
 parse_binance({'e':'depthUpdate','E':1000,'s':'BTCUSDT','u':1,'b':[['99','3']],'a':[['102','4']]},r,s)
 assert ('binance','BTCUSDT','bid',99.0) in s.levels
 parse_binance({'e':'bookTicker','E':1001,'s':'BTCUSDT','u':2,'b':'100','B':'2','a':'101','A':'3'},r,s)
 assert ('binance','BTCUSDT','bid',99.0) in s.levels
