from __future__ import annotations
from dataclasses import asdict
from typing import Dict, Iterable, List, Optional, Tuple
from market_physics_v3.schema import BookEvent, TradeEvent, DerivativeEvent

MS = 1_000_000

def _ns_ms(x): return int(x) * MS

def _clock(event_ms, receive_ns):
    event_ns = _ns_ms(event_ms)
    # Local receive clock can be microscopically before an exchange timestamp because of
    # clock skew. Preserve causality by clamping only the recorded receive timestamp;
    # raw collector metadata should separately monitor NTP skew.
    recv=int(receive_ns)
    if recv < event_ns:
        raise ValueError('negative transport latency; check NTP clock sync')
    return event_ns, recv

class SequenceGap(RuntimeError):
    pass

class BookDeltaState:
    def __init__(self):
        self.levels = {}
        self.initialized = set()
        self.sequence = {}
    def reset_snapshot(self, venue, symbol):
        prefix=(venue,symbol)
        self.levels={k:v for k,v in self.levels.items() if k[:2] != prefix}
        self.initialized.add(prefix)
    def validate_sequence(self, venue, symbol, sequence, previous=None, snapshot=False):
        key=(venue,symbol)
        seq=int(sequence)
        old=self.sequence.get(key)
        if snapshot:
            self.sequence[key]=seq
            return
        if previous is not None and old is not None and int(previous) != old:
            raise SequenceGap('%s %s sequence gap: prev=%s expected=%s' % (venue,symbol,previous,old))
        if previous is None and old is not None and seq <= old:
            raise SequenceGap('%s %s non-monotonic sequence: %s <= %s' % (venue,symbol,seq,old))
        self.sequence[key]=seq
    def classify(self, venue, symbol, side, price, qty, snapshot=False):
        key=(venue,symbol,side,float(price)); old=self.levels.get(key)
        q=float(qty); known=(venue,symbol) in self.initialized
        if snapshot:
            typ='snapshot'; self.levels[key]=q
        elif q == 0.0:
            typ='remove'; self.levels.pop(key,None)
        elif old is None and known:
            typ='add'; self.levels[key]=q
        else:
            typ='modify'; self.levels[key]=q
        return typ


def canonical_symbol(symbol):
    s=str(symbol).upper()
    if s.endswith('-USDT-SWAP'):
        return s[:-10]+'USDT'
    if s.endswith('-USD-SWAP'):
        return s[:-9]+'USD'
    if '-' in s:
        return s.replace('-','')
    if not s.endswith(('USDT','USDC','USD')) and '-' not in s:
        return s+'USDT'
    return s

def _level(row):
    if isinstance(row,dict):
        return float(row['px']), float(row['sz'])
    return float(row[0]), float(row[1])

def _book_rows(venue,symbol,event_ms,receive_ns,sequence,bids,asks,state,snapshot=False):
    symbol=canonical_symbol(symbol)
    if snapshot:
        state.reset_snapshot(venue,symbol)
    event_ns, recv=_clock(event_ms,receive_ns); out=[]
    for side, rows in [('bid',bids or []),('ask',asks or [])]:
        for row in rows:
            px,qty=_level(row); typ=state.classify(venue,symbol,side,px,qty,snapshot)
            out.append(BookEvent(venue,symbol,event_ns,recv,int(sequence),typ,side,px,qty))
    return out


def parse_binance(msg, receive_ns, state):
    d=msg.get('data',msg); typ=d.get('e'); out=[]
    if typ=='depthUpdate':
        sym=canonical_symbol(d['s']); state.validate_sequence('binance',sym,d['u'],d.get('pu'),False)
        event_ms=d['T'] if 'T' in d else d['E']
        out += _book_rows('binance',sym,event_ms,receive_ns,d['u'],d.get('b'),d.get('a'),state,False)
    elif typ=='bookTicker':
        # BBO is a full one-level snapshot. Some USD-M payload variants carry
        # E/T while others only expose update id; without exchange time we use
        # receive time as the conservative availability timestamp.
        sym=canonical_symbol(d['s']); event_ms=int(d.get('T') or d.get('E') or receive_ns//MS); seq=int(d.get('u') or event_ms)
        out += _book_rows('binance',sym,event_ms,receive_ns,seq,[[d['b'],d['B']]],[[d['a'],d['A']]],state,True)
    elif typ in {'aggTrade','trade'}:
        event_ns,recv=_clock(d.get('T',d['E']),receive_ns)
        # m=True means buyer is maker, therefore aggressor is sell.
        out.append(TradeEvent('binance',d['s'],event_ns,recv,str(d.get('a',d.get('t'))),float(d['p']),float(d['q']),'sell' if d.get('m') else 'buy'))
    elif typ=='forceOrder':
        o=d['o']; event_ns,recv=_clock(o.get('T',d['E']),receive_ns); px=float(o.get('ap') or o.get('p'))
        # SELL liquidation order closes a long; BUY closes a short.
        side='long' if o['S']=='SELL' else 'short'; value=float(o['q'])*px
        out.append(DerivativeEvent('binance',o['s'],event_ns,recv,'liquidation',value,side,px))
    elif typ=='markPriceUpdate':
        event_ns,recv=_clock(d['E'],receive_ns); sym=d['s']
        for k,field in [('mark','p'),('index','i'),('funding','r')]:
            if field in d and d[field] not in (None,''):
                out.append(DerivativeEvent('binance',sym,event_ns,recv,k,float(d[field])))
    return out


def parse_bybit(msg, receive_ns, state):
    topic=str(msg.get('topic','')); out=[]
    if topic.startswith('orderbook.'):
        d=msg['data']; event_ms=d.get('cts',msg.get('ts')); snapshot=msg.get('type')=='snapshot'; sym=canonical_symbol(d['s']); seq=d.get('seq',d.get('u',0))
        state.validate_sequence('bybit',sym,seq,None,snapshot)
        out += _book_rows('bybit',sym,event_ms,receive_ns,seq,d.get('b'),d.get('a'),state,snapshot)
    elif topic.startswith('publicTrade.'):
        for d in msg.get('data',[]):
            event_ns,recv=_clock(d['T'],receive_ns)
            out.append(TradeEvent('bybit',d['s'],event_ns,recv,str(d.get('i',d['T'])),float(d['p']),float(d['v']),'buy' if d['S']=='Buy' else 'sell'))
    elif topic.startswith('allLiquidation.'):
        for d in msg.get('data',[]):
            event_ns,recv=_clock(d['T'],receive_ns); px=float(d['p']); value=float(d['v'])*px
            # Bybit documents Buy as a liquidated long position.
            side='long' if d['S']=='Buy' else 'short'
            out.append(DerivativeEvent('bybit',d['s'],event_ns,recv,'liquidation',value,side,px))
    elif topic.startswith('tickers.'):
        rows=msg.get('data',[]); rows=rows if isinstance(rows,list) else [rows]
        for d in rows:
            event_ns,recv=_clock(msg['ts'],receive_ns); sym=d.get('symbol') or topic.split('.',1)[1]
            for kind,key in [('open_interest','openInterest'),('funding','fundingRate'),('mark','markPrice'),('index','indexPrice')]:
                if d.get(key) not in (None,''):
                    out.append(DerivativeEvent('bybit',sym,event_ns,recv,kind,float(d[key])))
    return out


def _okx_symbol(inst): return canonical_symbol(inst)
def parse_okx(msg, receive_ns, state):
    arg=msg.get('arg',{}); channel=arg.get('channel'); out=[]
    for d in msg.get('data',[]) or []:
        inst=d.get('instId') or arg.get('instId',''); sym=_okx_symbol(inst)
        if channel=='liquidation-orders':
            details=d.get('details') or [d]
            for x in details:
                ts=int(x.get('ts') or d.get('ts') or 0)
                if not ts:
                    continue
                event_ns,recv=_clock(ts,receive_ns); px=float(x.get('bkPx') or x.get('px')); sz=float(x.get('sz') or 0)
                pos_side=str(x.get('posSide') or '').lower()
                side=pos_side if pos_side in {'long','short'} else ('long' if x.get('side')=='sell' else 'short')
                out.append(DerivativeEvent('okx',sym,event_ns,recv,'liquidation',sz*px,side,px))
            continue
        event_ms=int(d.get('ts') or 0)
        if not event_ms: continue
        if channel in {'books','books5','bbo-tbt','books50-l2-tbt','books-l2-tbt'}:
            snapshot=msg.get('action')=='snapshot' or channel in {'books5','bbo-tbt'}
            seq=d.get('seqId',event_ms); state.validate_sequence('okx',sym,seq,d.get('prevSeqId'),snapshot)
            out += _book_rows('okx',sym,event_ms,receive_ns,seq,d.get('bids'),d.get('asks'),state,snapshot)
        elif channel=='trades':
            event_ns,recv=_clock(event_ms,receive_ns)
            out.append(TradeEvent('okx',sym,event_ns,recv,str(d.get('tradeId',event_ms)),float(d['px']),float(d['sz']),'buy' if d['side']=='buy' else 'sell'))
        elif channel=='open-interest':
            event_ns,recv=_clock(event_ms,receive_ns); out.append(DerivativeEvent('okx',sym,event_ns,recv,'open_interest',float(d.get('oiCcy') or d['oi'])))
        elif channel=='funding-rate':
            event_ns,recv=_clock(event_ms,receive_ns); out.append(DerivativeEvent('okx',sym,event_ns,recv,'funding',float(d['fundingRate'])))
        elif channel=='mark-price':
            event_ns,recv=_clock(event_ms,receive_ns); out.append(DerivativeEvent('okx',sym,event_ns,recv,'mark',float(d['markPx'])))
        elif channel=='index-tickers':
            event_ns,recv=_clock(event_ms,receive_ns); out.append(DerivativeEvent('okx',sym,event_ns,recv,'index',float(d['idxPx'])))
    return out


def parse_hyperliquid(msg, receive_ns, state):
    ch=msg.get('channel'); data=msg.get('data'); out=[]
    if ch=='l2Book' and data:
        sym=data['coin']; event_ms=data['time']; levels=data['levels']; seq=event_ms
        out += _book_rows('hyperliquid',sym,event_ms,receive_ns,seq,levels[0],levels[1],state,True)
    elif ch=='bbo' and data:
        sym=data['coin']; event_ms=data['time']; bbo=data.get('bbo') or [None,None]; bids=[] if bbo[0] is None else [[bbo[0]['px'],bbo[0]['sz']]]; asks=[] if bbo[1] is None else [[bbo[1]['px'],bbo[1]['sz']]]
        out += _book_rows('hyperliquid',sym,event_ms,receive_ns,event_ms,bids,asks,state,True)
    elif ch=='trades':
        for d in data or []:
            event_ns,recv=_clock(d['time'],receive_ns); side=str(d['side']).upper(); ag='buy' if side in {'B','BUY'} else 'sell'
            out.append(TradeEvent('hyperliquid',canonical_symbol(d['coin']),event_ns,recv,str(d.get('tid',d.get('hash'))),float(d['px']),float(d['sz']),ag))
    elif ch=='activeAssetCtx' and data:
        ctx=data['ctx']; sym=canonical_symbol(data['coin']); event_ms=int(ctx.get('time') or msg.get('time') or receive_ns//MS); event_ns,recv=_clock(event_ms,receive_ns)
        for kind,key in [('funding','funding'),('open_interest','openInterest'),('mark','markPx'),('index','oraclePx')]:
            if ctx.get(key) not in (None,''):
                out.append(DerivativeEvent('hyperliquid',sym,event_ns,recv,kind,float(ctx[key])))
    return out

PARSERS={'binance':parse_binance,'bybit':parse_bybit,'okx':parse_okx,'hyperliquid':parse_hyperliquid}

def event_record(event):
    d=asdict(event); d['_record_type']=event.__class__.__name__; return d
