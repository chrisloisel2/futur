from __future__ import annotations

from dataclasses import asdict

from market_physics_v3.schema import BookEvent, DerivativeEvent, TradeEvent

MS = 1_000_000
BBO_STREAMS = {'bbo', 'bookTicker', 'bbo-tbt'}

def _ns_ms(x): return int(x) * MS

def _clock(event_ms, receive_ns):
    event_ns = _ns_ms(event_ms)
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
    def validate_sequence(self, venue, symbol, sequence, previous=None, snapshot=False, stream=None):
        key=(venue,symbol,stream or '__default__')
        seq=int(sequence)
        old=self.sequence.get(key)
        if snapshot:
            self.sequence[key]=seq
            return
        if previous is not None and old is not None and int(previous) != old:
            raise SequenceGap('%s %s %s sequence gap: prev=%s expected=%s' % (
                venue,symbol,stream or 'default',previous,old))
        if previous is None and old is not None and seq <= old:
            raise SequenceGap('%s %s %s non-monotonic sequence: %s <= %s' % (
                venue,symbol,stream or 'default',seq,old))
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
        elif old is None:
            typ='update'; self.levels[key]=q
        else:
            typ='modify'; self.levels[key]=q
        return typ


def canonical_symbol(symbol):
    s=str(symbol).upper()
    if s.endswith('-USDT-SWAP'):
        return s[:-10]+'USDT'
    if s.endswith('-USD-SWAP'):
        return s[:-9]+'USD'
    if s.endswith('-PERPETUAL'):
        # Deribit perpetuals are coin-margined (inverse), not the linear USDT
        # contract other venues trade -- USD suffix keeps it a distinct symbol
        # rather than silently conflating two non-fungible instruments.
        return s[:-10]+'USD'
    if '-' in s:
        return s.replace('-','')
    if not s.endswith(('USDT','USDC','USD')) and '-' not in s:
        return s+'USDT'
    return s

def _level(row):
    if isinstance(row,dict):
        n=row.get('n')
        return float(row['px']), float(row['sz']), (None if n is None else int(n))
    return float(row[0]), float(row[1]), None

def _book_rows(
    venue,symbol,event_ms,receive_ns,sequence,bids,asks,state,
    snapshot=False,reset_state=True,source_stream=None,
    first_sequence_id=None,previous_sequence_id=None,
):
    symbol=canonical_symbol(symbol)
    source_stream=None if source_stream is None else str(source_stream)
    is_bbo=source_stream in BBO_STREAMS
    if snapshot and reset_state and not is_bbo:
        state.reset_snapshot(venue,symbol)
    event_ns, recv=_clock(event_ms,receive_ns); out=[]
    for side, rows in [('bid',bids or []),('ask',asks or [])]:
        for row in rows:
            px,qty,order_count=_level(row)
            typ='snapshot' if is_bbo and snapshot else state.classify(venue,symbol,side,px,qty,snapshot)
            out.append(BookEvent(
                venue,symbol,event_ns,recv,int(sequence),typ,side,px,qty,
                order_count=order_count,
                source_stream=source_stream,
                first_sequence_id=(None if first_sequence_id is None else int(first_sequence_id)),
                previous_sequence_id=(None if previous_sequence_id is None else int(previous_sequence_id)),
            ))
    return out


def parse_binance(msg, receive_ns, state):
    d=msg.get('data',msg); typ=d.get('e'); out=[]
    if typ=='depthUpdate':
        sym=canonical_symbol(d['s']); state.validate_sequence('binance',sym,d['u'],d.get('pu'),False,stream='depth')
        event_ms=d['T'] if 'T' in d else d['E']
        out += _book_rows(
            'binance',sym,event_ms,receive_ns,d['u'],d.get('b'),d.get('a'),state,False,
            source_stream='depth',first_sequence_id=d.get('U'),previous_sequence_id=d.get('pu')
        )
    elif typ=='bookTicker':
        sym=canonical_symbol(d['s']); event_ms=int(d.get('T') or d.get('E') or receive_ns//MS); seq=int(d.get('u') or event_ms)
        out += _book_rows(
            'binance',sym,event_ms,receive_ns,seq,[[d['b'],d['B']]],[[d['a'],d['A']]],state,True,False,
            source_stream='bookTicker'
        )
    elif typ in {'aggTrade','trade'}:
        event_ns,recv=_clock(d.get('T',d['E']),receive_ns)
        out.append(TradeEvent(
            'binance',canonical_symbol(d['s']),event_ns,recv,
            str(d.get('a',d.get('t'))),float(d['p']),float(d['q']),
            'sell' if d.get('m') else 'buy',
            source_stream=typ,
            granularity=('aggregate' if typ=='aggTrade' else 'individual'),
        ))
    elif typ=='forceOrder':
        o=d['o']; event_ns,recv=_clock(o.get('T',d['E']),receive_ns); px=float(o.get('ap') or o.get('p'))
        side='long' if o['S']=='SELL' else 'short'; value=float(o['q'])*px
        out.append(DerivativeEvent('binance',canonical_symbol(o['s']),event_ns,recv,'liquidation',value,side,px))
    elif typ=='markPriceUpdate':
        event_ns,recv=_clock(d['E'],receive_ns); sym=canonical_symbol(d['s'])
        for k,field in [('mark','p'),('index','i'),('funding','r')]:
            if field in d and d[field] not in (None,''):
                out.append(DerivativeEvent('binance',sym,event_ns,recv,k,float(d[field])))
    return out


def parse_bybit(msg, receive_ns, state):
    topic=str(msg.get('topic','')); out=[]
    if topic.startswith('orderbook.'):
        d=msg['data']; event_ms=d.get('cts',msg.get('ts')); snapshot=msg.get('type')=='snapshot'; sym=canonical_symbol(d['s']); seq=d.get('seq',d.get('u',0))
        stream='.'.join(topic.split('.')[:2])
        state.validate_sequence('bybit',sym,seq,None,snapshot,stream=stream)
        out += _book_rows('bybit',sym,event_ms,receive_ns,seq,d.get('b'),d.get('a'),state,snapshot,source_stream=stream)
    elif topic.startswith('publicTrade.'):
        for d in msg.get('data',[]):
            event_ns,recv=_clock(d['T'],receive_ns)
            out.append(TradeEvent(
                'bybit',canonical_symbol(d['s']),event_ns,recv,str(d.get('i',d['T'])),
                float(d['p']),float(d['v']),'buy' if d['S']=='Buy' else 'sell',
                source_stream='publicTrade',granularity='individual'
            ))
    elif topic.startswith('allLiquidation.'):
        for d in msg.get('data',[]):
            event_ns,recv=_clock(d['T'],receive_ns); px=float(d['p']); value=float(d['v'])*px
            side='long' if d['S']=='Buy' else 'short'
            out.append(DerivativeEvent('bybit',canonical_symbol(d['s']),event_ns,recv,'liquidation',value,side,px))
    elif topic.startswith('tickers.'):
        rows=msg.get('data',[]); rows=rows if isinstance(rows,list) else [rows]
        for d in rows:
            event_ns,recv=_clock(msg['ts'],receive_ns); sym=canonical_symbol(d.get('symbol') or topic.split('.',1)[1])
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
            seq=d.get('seqId',event_ms)
            if channel in {'books','books50-l2-tbt','books-l2-tbt'}:
                state.validate_sequence('okx',sym,seq,d.get('prevSeqId'),snapshot,stream=channel)
            reset_state = channel not in {'books5','bbo-tbt'}
            out += _book_rows(
                'okx',sym,event_ms,receive_ns,seq,d.get('bids'),d.get('asks'),
                state,snapshot,reset_state,source_stream=channel,
                previous_sequence_id=d.get('prevSeqId')
            )
        elif channel=='trades':
            event_ns,recv=_clock(event_ms,receive_ns)
            out.append(TradeEvent(
                'okx',sym,event_ns,recv,str(d.get('tradeId',event_ms)),float(d['px']),float(d['sz']),
                'buy' if d['side']=='buy' else 'sell',source_stream='trades',granularity='individual'
            ))
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
        out += _book_rows('hyperliquid',sym,event_ms,receive_ns,seq,levels[0],levels[1],state,True,source_stream='l2Book')
    elif ch=='bbo' and data:
        sym=data['coin']; event_ms=data['time']; bbo=data.get('bbo') or [None,None]
        bids=[] if bbo[0] is None else [bbo[0]]; asks=[] if bbo[1] is None else [bbo[1]]
        out += _book_rows('hyperliquid',sym,event_ms,receive_ns,event_ms,bids,asks,state,True,False,source_stream='bbo')
    elif ch=='trades':
        for d in data or []:
            event_ns,recv=_clock(d['time'],receive_ns); side=str(d['side']).upper(); ag='buy' if side in {'B','BUY'} else 'sell'
            symbol=canonical_symbol(d['coin']); tid=d.get('tid')
            trade_id=(('%s:%s:%s' % (d['time'],symbol,tid)) if tid is not None else str(d.get('hash')))
            users=d.get('users') or [None,None]
            buyer=users[0] if len(users)>0 else None; seller=users[1] if len(users)>1 else None
            out.append(TradeEvent(
                'hyperliquid',symbol,event_ns,recv,trade_id,float(d['px']),float(d['sz']),ag,
                buyer=buyer,seller=seller,tx_hash=d.get('hash'),source_stream='trades',granularity='individual'
            ))
    elif ch=='activeAssetCtx' and data:
        ctx=data['ctx']; sym=canonical_symbol(data['coin'])
        event_ms=int(ctx.get('time') or msg.get('time') or receive_ns//MS); event_ns,recv=_clock(event_ms,receive_ns)
        for kind,key in [('funding','funding'),('open_interest','openInterest'),('mark','markPx'),('index','oraclePx')]:
            if ctx.get(key) not in (None,''):
                out.append(DerivativeEvent('hyperliquid',sym,event_ns,recv,kind,float(ctx[key])))
    return out

def _deribit_strip_action(rows):
    # Deribit book rows are [action, price, amount] where action is
    # new/change/delete; _level()/state.classify() derive event type from
    # qty==0 the same way every other venue does, so action is only used
    # here to force qty=0 on "delete" (some payloads carry a stale amount).
    out=[]
    for row in rows or []:
        action,price,amount=row[0],row[1],row[2]
        out.append([price, 0.0 if action=='delete' else amount])
    return out


def parse_deribit(msg, receive_ns, state):
    if msg.get('method')!='subscription':
        return []
    params=msg.get('params') or {}; channel=str(params.get('channel','')); d=params.get('data'); out=[]
    if channel.startswith('book.') and d:
        sym=canonical_symbol(d['instrument_name']); snapshot=d.get('type')=='snapshot'
        change_id=d.get('change_id'); prev=d.get('prev_change_id')
        state.validate_sequence('deribit',sym,change_id,prev,snapshot,stream='book')
        out += _book_rows(
            'deribit',sym,d['timestamp'],receive_ns,change_id,
            _deribit_strip_action(d.get('bids')),_deribit_strip_action(d.get('asks')),
            state,snapshot,source_stream='book',previous_sequence_id=prev,
        )
    elif channel.startswith('trades.') and d:
        for t in d:
            sym=canonical_symbol(t['instrument_name']); event_ns,recv=_clock(t['timestamp'],receive_ns)
            out.append(TradeEvent(
                'deribit',sym,event_ns,recv,str(t['trade_id']),float(t['price']),float(t['amount']),
                'buy' if t['direction']=='buy' else 'sell',source_stream='trades',granularity='individual',
            ))
            # mark/index ride along on every trade tick -- cheap freshness between ticker beats.
            if t.get('mark_price') not in (None,''):
                out.append(DerivativeEvent('deribit',sym,event_ns,recv,'mark',float(t['mark_price'])))
            if t.get('index_price') not in (None,''):
                out.append(DerivativeEvent('deribit',sym,event_ns,recv,'index',float(t['index_price'])))
    elif channel.startswith('ticker.') and d:
        sym=canonical_symbol(d['instrument_name']); event_ns,recv=_clock(d['timestamp'],receive_ns)
        for kind,key in [('mark','mark_price'),('index','index_price'),('open_interest','open_interest'),('funding','current_funding')]:
            if d.get(key) not in (None,''):
                out.append(DerivativeEvent('deribit',sym,event_ns,recv,kind,float(d[key])))
    return out


PARSERS={'binance':parse_binance,'bybit':parse_bybit,'okx':parse_okx,'hyperliquid':parse_hyperliquid,'deribit':parse_deribit}

def event_record(event):
    d=asdict(event); d['_record_type']=event.__class__.__name__; return d
