import json

from market_physics_v3.collectors.normalize import (
 BookDeltaState,
 SequenceGap,
 canonical_symbol,
 parse_binance,
 parse_bybit,
 parse_deribit,
 parse_hyperliquid,
 parse_okx,
)
from market_physics_v3.collectors.runtime import _required_reply
from market_physics_v3.collectors.specs import subscriptions
from market_physics_v3.collectors.writer import AppendOnlyEventWriter
from market_physics_v3.schema import BookEvent, DerivativeEvent, TradeEvent

R=2_000_000_000_000_000_000

def test_binance_depth_trade_liq_mark():
 s=BookDeltaState();
 b=parse_binance({'e':'depthUpdate','E':1000,'s':'BTCUSDT','u':2,'b':[['100','2']],'a':[['101','3']]},R,s)
 assert len(b)==2 and b[0].event_type=='update'
 t=parse_binance({'e':'aggTrade','E':1001,'T':1001,'s':'BTCUSDT','a':9,'p':'100','q':'2','m':True},R,s)[0]; assert t.aggressor=='sell' and t.granularity=='aggregate'
 l=parse_binance({'e':'forceOrder','E':1002,'o':{'T':1002,'s':'BTCUSDT','S':'SELL','q':'2','p':'99','ap':'98'}},R,s)[0]; assert l.side=='long' and l.value==196
 m=parse_binance({'e':'markPriceUpdate','E':1003,'s':'BTCUSDT','p':'100','i':'99','r':'0.0001'},R,s); assert {x.kind for x in m}=={'mark','index','funding'}

def test_bybit_all_streams():
 s=BookDeltaState(); msg={'topic':'orderbook.50.BTCUSDT','type':'snapshot','ts':1000,'data':{'s':'BTCUSDT','b':[['100','1']],'a':[['101','2']],'u':1,'seq':2,'cts':999}}
 assert parse_bybit(msg,R,s)[0].event_type=='snapshot'
 tr=parse_bybit({'topic':'publicTrade.BTCUSDT','data':[{'T':1001,'s':'BTCUSDT','S':'Buy','v':'2','p':'100','i':'x'}]},R,s)[0]; assert tr.aggressor=='buy' and tr.granularity=='individual'
 liq=parse_bybit({'topic':'allLiquidation.BTCUSDT','data':[{'T':1002,'s':'BTCUSDT','S':'Buy','v':'2','p':'90'}]},R,s)[0]; assert liq.side=='long'
 tick=parse_bybit({'topic':'tickers.BTCUSDT','ts':1003,'data':{'symbol':'BTCUSDT','openInterest':'10','fundingRate':'0.01','markPrice':'100','indexPrice':'99'}},R,s); assert len(tick)==4

def test_okx_and_hyperliquid():
 s=BookDeltaState(); o=parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'snapshot','data':[{'ts':'1000','seqId':3,'prevSeqId':-1,'bids':[['100','1','0','1']],'asks':[['101','1','0','1']]}]},R,s); assert len(o)==2
 tr=parse_okx({'arg':{'channel':'trades','instId':'BTC-USDT-SWAP'},'data':[{'ts':'1001','tradeId':'1','px':'100','sz':'2','side':'sell'}]},R,s)[0]; assert tr.symbol=='BTCUSDT' and tr.aggressor=='sell' and tr.granularity=='individual'
 h=parse_hyperliquid({'channel':'l2Book','data':{'coin':'BTC','time':1002,'levels':[[{'px':'100','sz':'2','n':1}],[{'px':'101','sz':'3','n':1}]]}},R,s); assert len(h)==2 and h[0].symbol=='BTCUSDT'
 ht=parse_hyperliquid({'channel':'trades','data':[{'coin':'BTC','time':1003,'side':'B','px':'100','sz':'1','tid':7}]},R,s)[0]; assert ht.aggressor=='buy' and ht.granularity=='individual'
 hc=parse_hyperliquid({'channel':'activeAssetCtx','data':{'coin':'BTC','ctx':{'funding':'0.001','openInterest':'10','markPx':'100','oraclePx':'99'}}},R,s); assert {x.kind for x in hc}=={'funding','open_interest','mark','index'}

def test_deribit_symbol_is_distinct_from_linear_usdt():
 assert canonical_symbol('BTC-PERPETUAL')=='BTCUSD'  # coin-margined, not fungible with BTCUSDT

def test_deribit_book_snapshot_then_change():
 s=BookDeltaState()
 snap=parse_deribit({'method':'subscription','params':{'channel':'book.BTC-PERPETUAL.100ms','data':{
   'type':'snapshot','timestamp':1000,'instrument_name':'BTC-PERPETUAL','change_id':100,
   'bids':[['new',100,2]],'asks':[['new',101,3]]}}},R,s)
 assert len(snap)==2 and snap[0].event_type=='snapshot' and snap[0].symbol=='BTCUSD'
 chg=parse_deribit({'method':'subscription','params':{'channel':'book.BTC-PERPETUAL.100ms','data':{
   'type':'change','timestamp':1001,'instrument_name':'BTC-PERPETUAL','change_id':101,'prev_change_id':100,
   'bids':[['change',100,5]],'asks':[['delete',101,0]]}}},R,s)
 assert len(chg)==2
 removed=[e for e in chg if e.side=='ask'][0]; assert removed.event_type=='remove' and removed.qty==0.0

def test_deribit_sequence_gap_is_fail_closed():
 s=BookDeltaState()
 parse_deribit({'method':'subscription','params':{'channel':'book.BTC-PERPETUAL.100ms','data':{
   'type':'snapshot','timestamp':1000,'instrument_name':'BTC-PERPETUAL','change_id':100,
   'bids':[['new',100,2]],'asks':[]}}},R,s)
 try:
  parse_deribit({'method':'subscription','params':{'channel':'book.BTC-PERPETUAL.100ms','data':{
    'type':'change','timestamp':1002,'instrument_name':'BTC-PERPETUAL','change_id':102,'prev_change_id':101,
    'bids':[],'asks':[]}}},R,s)
  assert False, 'expected SequenceGap'
 except SequenceGap:
  pass

def test_deribit_trade_carries_mark_and_index_and_ticker_has_funding():
 s=BookDeltaState()
 tr=parse_deribit({'method':'subscription','params':{'channel':'trades.BTC-PERPETUAL.100ms','data':[
   {'trade_id':'1','timestamp':1000,'price':100,'amount':2,'direction':'sell','mark_price':100.1,'index_price':99.9,'instrument_name':'BTC-PERPETUAL'}]}},R,s)
 assert {e.__class__.__name__ for e in tr}=={'TradeEvent','DerivativeEvent'}
 trade=[e for e in tr if isinstance(e,TradeEvent)][0]; assert trade.aggressor=='sell' and trade.granularity=='individual'
 assert {e.kind for e in tr if isinstance(e,DerivativeEvent)}=={'mark','index'}
 tick=parse_deribit({'method':'subscription','params':{'channel':'ticker.BTC-PERPETUAL.100ms','data':{
   'timestamp':1001,'instrument_name':'BTC-PERPETUAL','mark_price':100,'index_price':99,'open_interest':500,'current_funding':0.0001}}},R,s)
 assert {e.kind for e in tick}=={'mark','index','open_interest','funding'}

def test_deribit_ignores_non_subscription_messages():
 s=BookDeltaState()
 assert parse_deribit({'jsonrpc':'2.0','id':3600,'result':['book.BTC-PERPETUAL.100ms']},R,s)==[]

def test_deribit_required_reply_answers_test_request_and_ignores_everything_else():
 reply=_required_reply('deribit',{'jsonrpc':'2.0','method':'test_request','params':{},'id':7})
 assert reply=={'jsonrpc':'2.0','id':7,'method':'public/test','params':{}}
 assert _required_reply('deribit',{'method':'subscription'}) is None
 assert _required_reply('bybit',{'method':'test_request'}) is None  # venue-scoped, not a generic hook

def test_deribit_subscribe_skips_symbols_without_an_instrument_and_sets_heartbeat():
 spec=subscriptions('deribit',['BTCUSDT','ETHUSDT','SOLUSDT'])
 msgs={m['method']:m for m in spec['subscribe_many']}
 channels=msgs['public/subscribe']['params']['channels']
 assert any(c.startswith('book.BTC-PERPETUAL') for c in channels)
 assert any(c.startswith('book.ETH-PERPETUAL') for c in channels)
 assert not any('SOL' in c for c in channels)  # no Deribit SOL perpetual -- must not fabricate one
 assert msgs['public/set_heartbeat']['params']['interval']==30


def test_subscriptions_official_shapes():
 b=subscriptions('binance',['BTCUSDT']); conns={x['name']:x for x in b['connections']}
 assert set(conns)=={'public','market'}
 assert conns['public']['url'].endswith('/public/ws') and conns['market']['url'].endswith('/market/ws')
 public_params=conns['public']['subscribe']['params']; market_params=conns['market']['subscribe']['params']
 assert 'btcusdt@depth@100ms' in public_params and 'btcusdt@bookTicker' in public_params
 assert 'btcusdt@aggTrade' not in public_params and 'btcusdt@markPrice@1s' not in public_params
 assert 'btcusdt@aggTrade' in market_params and 'btcusdt@markPrice@1s' in market_params and 'btcusdt@forceOrder' in market_params
 assert 'btcusdt@depth@100ms' not in market_params
 assert 'orderbook.50.BTCUSDT' in subscriptions('bybit',['BTCUSDT'])['subscribe']['args']
 okx_args=subscriptions('okx',['BTCUSDT'])['subscribe']['args']
 assert okx_args[0]['instId']=='BTC-USDT-SWAP'
 assert {'channel':'index-tickers','instId':'BTC-USDT'} in okx_args
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

 from market_physics_v3.collectors.normalize import BookDeltaState, SequenceGap, parse_okx
 s=BookDeltaState(); r=2_000_000_000_000_000_000
 parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'snapshot','data':[{'ts':'1000','seqId':10,'prevSeqId':-1,'bids':[['100','1','0','1']],'asks':[['101','1','0','1']]}]},r,s)
 with pytest.raises(SequenceGap):
  parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'update','data':[{'ts':'1001','prevSeqId':9,'seqId':11,'bids':[['100','2','0','1']],'asks':[]}]},r,s)

def test_okx_bbo_does_not_advance_books_sequence_and_resets_are_valid():
 s=BookDeltaState(); r=2_000_000_000_000_000_000
 parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'snapshot','data':[{'ts':'1000','prevSeqId':-1,'seqId':10,'bids':[['100','1','0','1']],'asks':[['101','1','0','1']]}]},r,s)
 parse_okx({'arg':{'channel':'bbo-tbt','instId':'BTC-USDT-SWAP'},'data':[{'ts':'1001','seqId':23,'bids':[['100','2','0','1']],'asks':[['101','2','0','1']]}]},r,s)
 assert s.sequence[('okx','BTCUSDT','books')] == 10
 parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'update','data':[{'ts':'1002','prevSeqId':10,'seqId':15,'bids':[['100','2','0','1']],'asks':[]}]},r,s)
 parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'update','data':[{'ts':'1003','prevSeqId':15,'seqId':15,'bids':[],'asks':[]}]},r,s)
 parse_okx({'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'action':'update','data':[{'ts':'1004','prevSeqId':15,'seqId':3,'bids':[['99','1','0','1']],'asks':[]}]},r,s)
 assert s.sequence[('okx','BTCUSDT','books')] == 3

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
 from market_physics_v3.microstructure import cancellation_imbalance, removal_imbalance
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


def _make_qualified_bybit_fixture(tmp_path, dead_letter=False, clean_shutdown=None):
 health_dir = tmp_path / 'health'
 health_dir.mkdir(parents=True)
 root = tmp_path / 'data'
 for kind in ['book_events','trades','derivatives']:
  p = root / 'raw' / kind / 'venue=bybit' / 'symbol=BTCUSDT' / 'date=2026-08-15'
  p.mkdir(parents=True)
  (p / 'events.jsonl').write_text('{}\n')
 raw = root / 'raw_wire' / 'venue=bybit' / 'date=2026-08-15'
 raw.mkdir(parents=True)
 (raw / 'messages.jsonl').write_text('{}\n')
 if dead_letter:
  dl = root / 'dead_letters' / 'venue=bybit' / 'date=2026-08-15'
  dl.mkdir(parents=True)
  (dl / 'errors.jsonl').write_text('{"error":"x"}\n')
 health = {
  'connected': False,
  'events': 7452,
  'idle_ms': 201.0,
  'last_event_ns': 1786807575553000000,
  'last_exception': None,
  'last_receive_ns': 1786807575641313493,
  'messages': 2244,
  'parse_errors': 0,
  'reconnects': 0,
  'sequence_gaps': 0,
  'subscription_acks': 2,
  'subscription_errors': 0,
  'venue': 'bybit',
 }
 if clean_shutdown is not None:
  health['clean_shutdown'] = clean_shutdown
 (health_dir / 'bybit.json').write_text(json.dumps(health))
 return root, health_dir


def test_venue_qualifier_promotes_proven_live_feed(tmp_path):
 import pandas as pd

 from market_physics_v3.collectors.qualification import promote_manifest, qualify_venue
 root, health_dir = _make_qualified_bybit_fixture(tmp_path)
 report = qualify_venue('bybit', str(root), str(health_dir))
 assert report['qualified'] and report['reasons'] == []
 manifest = tmp_path / 'manifest.csv'
 pd.DataFrame([{'feed':'bybit','status':'UNKNOWN','notes':''}]).to_csv(manifest,index=False)
 assert promote_manifest(report, str(manifest))
 row = pd.read_csv(manifest).iloc[0]
 assert row['status'] == 'EVENT_LEVEL'


def test_venue_qualifier_blocks_dead_letters_and_unclean_shutdown(tmp_path):
 from market_physics_v3.collectors.qualification import qualify_venue
 root, health_dir = _make_qualified_bybit_fixture(tmp_path, dead_letter=True, clean_shutdown=False)
 report = qualify_venue('bybit', str(root), str(health_dir))
 assert not report['qualified']
 assert 'nonempty_dead_letters' in report['reasons']
 assert 'unclean_shutdown' in report['reasons']


def test_venue_qualifier_new_health_does_not_reuse_stale_type_files(tmp_path):
 from market_physics_v3.collectors.qualification import qualify_venue
 root, health_dir = _make_qualified_bybit_fixture(tmp_path, clean_shutdown=True)
 hp=health_dir/'bybit.json'; health=json.loads(hp.read_text())
 health.update({'book_events':100,'trade_events':0,'derivative_events':100})
 hp.write_text(json.dumps(health))
 report=qualify_venue('bybit',str(root),str(health_dir))
 assert not report['qualified'] and 'missing_trades' in report['reasons']


def test_cli_scripts_bootstrap_repo_root():
 import subprocess
 import sys
 from pathlib import Path
 root = Path(__file__).resolve().parents[2]
 for rel in [
  "scripts/build_market_physics_external_v3.py",
  "scripts/collect_market_physics_v3.py",
  "scripts/qualify_market_physics_feed_v3.py",
 ]:
  p = subprocess.run(
   [sys.executable, str(root / rel), "--help"],
   cwd=str(root),
   stdout=subprocess.PIPE,
   stderr=subprocess.PIPE,
   text=True,
  )
  assert p.returncode == 0, "%s failed: %s" % (rel, p.stderr)
