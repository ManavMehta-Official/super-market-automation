#!/usr/bin/env python3
"""SAS local coursework demonstration. Python 3.10+, no third-party runtime packages."""
import argparse, hashlib, json, sqlite3, uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
D = Decimal
class ValidationError(ValueError): pass

def decimal(value):
    try: n = D(str(value))
    except (InvalidOperation, ValueError): raise ValidationError('Enter a valid decimal number.')
    if not n.is_finite() or n.copy_abs() > D('100000000'): raise ValidationError('Number is outside the supported range.')
    return n

def money(value):
    n=decimal(value)
    if n < 0 or n.as_tuple().exponent < -2: raise ValidationError('Money must be nonnegative with at most two decimal places.')
    return int(n*100)

def quantity(value, unit):
    n=decimal(value)
    if n <= 0: raise ValidationError('Quantity must be greater than zero.')
    if unit not in ('each','kg'): raise ValidationError('Unsupported unit.')
    if unit=='each' and n != n.to_integral_value(): raise ValidationError('Counted items need a whole-number quantity.')
    if unit=='kg' and n.as_tuple().exponent < -3: raise ValidationError('Weight supports at most three decimal places.')
    return n

def cents(amount): return int(amount.quantize(D('1'), rounding=ROUND_HALF_UP))
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def fingerprint(data): return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class Store:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as c:
            c.executescript('''
CREATE TABLE IF NOT EXISTS items(code TEXT PRIMARY KEY, barcode TEXT UNIQUE NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL, stock TEXT NOT NULL, price_cents INTEGER NOT NULL, avg_cost TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sales(serial TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL, completed_at TEXT NOT NULL, total_cents INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sale_lines(serial TEXT NOT NULL REFERENCES sales(serial), line_no INTEGER NOT NULL, code TEXT NOT NULL REFERENCES items(code), name TEXT NOT NULL, unit TEXT NOT NULL, quantity TEXT NOT NULL, price_cents INTEGER NOT NULL, cost_snapshot TEXT NOT NULL, revenue_cents INTEGER NOT NULL, cost_cents INTEGER NOT NULL, PRIMARY KEY(serial,line_no));
CREATE TABLE IF NOT EXISTS receipts(reference TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, code TEXT NOT NULL, quantity TEXT NOT NULL, unit_cost TEXT NOT NULL, actor TEXT NOT NULL, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS price_audit(id INTEGER PRIMARY KEY, code TEXT NOT NULL, old_cents INTEGER NOT NULL, new_cents INTEGER NOT NULL, actor TEXT NOT NULL, changed_at TEXT NOT NULL);
''')
            c.executemany('INSERT OR IGNORE INTO items(code,barcode,name,unit,stock,price_cents,avg_cost) VALUES(?,?,?,?,?,?,?)',[
                ('MILK','1001','Fresh milk · 1 litre','each','42',6200,'48.000000'),('BREAD','1002','Whole wheat bread','each','30',4500,'31.000000'),('RICE','1003','Basmati rice','kg','75.500',12500,'92.000000'),('APPLE','1004','Royal Gala apples','kg','24.750',18000,'125.000000'),('EGGS','1005','Farm eggs · pack of 6','each','36',7200,'54.000000'),('COFFEE','1006','Ground coffee · 200 g','each','18',24500,'180.000000')])
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10); c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); return c
    def role(self,actual,expected):
        if actual != expected: raise PermissionError('Switch to the '+expected+' demo role for this operation.')
    def item(self,c,code):
        row=c.execute('SELECT * FROM items WHERE code=?',(code,)).fetchone()
        if not row: raise ValidationError('Unknown item code.')
        return dict(row)
    def items(self):
        with self.connect() as c: return [dict(r) for r in c.execute('SELECT * FROM items ORDER BY code')]
    def sale(self,serial,c=None):
        if c is None:
            with self.connect() as con: return self.sale(serial,con)
        row=c.execute('SELECT serial,completed_at,total_cents FROM sales WHERE serial=?',(serial,)).fetchone()
        if not row: raise ValidationError('Receipt not found.')
        result=dict(row); result['lines']=[dict(r) for r in c.execute('SELECT line_no,code,name,unit,quantity,price_cents,cost_snapshot,revenue_cents,cost_cents FROM sale_lines WHERE serial=? ORDER BY line_no',(serial,))]; return result
    def sales(self):
        with self.connect() as c: return [dict(r) for r in c.execute('SELECT serial,completed_at,total_cents FROM sales ORDER BY completed_at DESC,rowid DESC LIMIT 25')]
    def checkout(self,data,role):
        self.role(role,'clerk')
        key=data.get('request_id',''); lines=data.get('lines')
        if not isinstance(key,str) or not 1 <= len(key) <= 128: raise ValidationError('A request identifier is required.')
        if not isinstance(lines,list) or not 1 <= len(lines) <= 200: raise ValidationError('Add between 1 and 200 sale lines.')
        digest=fingerprint(data)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT serial,payload_hash FROM sales WHERE request_id=?',(key,)).fetchone()
            if old:
                if old['payload_hash']!=digest: raise ValidationError('Request identifier already used for different sale details.')
                return self.sale(old['serial'],c)
            required={}; prepared=[]
            for line in lines:
                if not isinstance(line,dict): raise ValidationError('Invalid sale line.')
                item=self.item(c,line.get('code')); unit=item['unit']
                if line.get('unit')!=unit: raise ValidationError('Item unit does not match.')
                q=quantity(line.get('quantity'),unit)
                if unit=='kg' and line.get('stable') is not True: raise ValidationError('Simulated scale must have a stable reading.')
                price=line.get('price_cents')
                if type(price) is not int or price < 0 or price > 10000000000: raise ValidationError('Invalid captured price.')
                # Draft price is checked against current catalogue; refresh after a price change.
                if price != item['price_cents']: raise ValidationError('Price changed. Remove and re-add this item before checkout.')
                required[item['code']]=required.get(item['code'],D(0))+q
                prepared.append((item,q,price,cents(q*price),cents(q*D(item['avg_cost'])*100)))
            for code,q in required.items():
                if q > D(self.item(c,code)['stock']): raise ValidationError('Insufficient stock for '+code+'.')
            serial='SAS-'+datetime.now(timezone.utc).strftime('%Y%m%d')+'-'+uuid.uuid4().hex[:8].upper()
            c.execute('INSERT INTO sales VALUES(?,?,?,?,?)',(serial,key,digest,now(),sum(p[3] for p in prepared)))
            for idx,(item,q,price,revenue,cost) in enumerate(prepared,1):
                c.execute('INSERT INTO sale_lines VALUES(?,?,?,?,?,?,?,?,?,?)',(serial,idx,item['code'],item['name'],item['unit'],str(q),price,item['avg_cost'],revenue,cost))
            for code,q in required.items():
                remaining=D(self.item(c,code)['stock'])-q
                c.execute('UPDATE items SET stock=? WHERE code=?',(str(remaining),code))
            return self.sale(serial,c)
    def restock(self,data,role):
        self.role(role,'employee'); ref=data.get('reference','')
        if not isinstance(ref,str) or not ref.strip() or len(ref)>128: raise ValidationError('Enter a receipt reference (up to 128 characters).')
        digest=fingerprint(data)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM receipts WHERE reference=?',(ref,)).fetchone()
            if old:
                if old['payload_hash']!=digest: raise ValidationError('Receipt reference already used with different details.')
                return self.item(c,old['code'])
            item=self.item(c,data.get('code'))
            if data.get('unit')!=item['unit']: raise ValidationError('Item unit does not match.')
            q=quantity(data.get('quantity'),item['unit']); cost=D(money(data.get('cost')))/100
            stock=D(item['stock']); newstock=stock+q
            avg=((stock*D(item['avg_cost'])+q*cost)/newstock).quantize(D('.000001'),rounding=ROUND_HALF_UP)
            c.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?)',(ref,digest,item['code'],str(q),str(cost),'demo-employee',now()))
            c.execute('UPDATE items SET stock=?,avg_cost=? WHERE code=?',(str(newstock),str(avg),item['code']))
            return self.item(c,item['code'])
    def price(self,data,role):
        self.role(role,'manager'); price=money(data.get('price'))
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE'); item=self.item(c,data.get('code'))
            if type(data.get('version')) is not int or data['version']!=item['version']: raise ValidationError('Price was updated elsewhere. Refresh inventory and try again.')
            c.execute('UPDATE items SET price_cents=?,version=version+1 WHERE code=?',(price,item['code']))
            c.execute('INSERT INTO price_audit(code,old_cents,new_cents,actor,changed_at) VALUES(?,?,?,?,?)',(item['code'],item['price_cents'],price,'demo-manager',now()))
            return self.item(c,item['code'])
    def audit(self,role):
        if role not in ('employee','manager'): raise PermissionError('Audit is available to employee and manager demo roles.')
        with self.connect() as c:
            return {'receipts':[dict(r) for r in c.execute('SELECT reference,code,quantity,unit_cost,actor,received_at FROM receipts ORDER BY rowid DESC LIMIT 25')], 'prices':[dict(r) for r in c.execute('SELECT code,old_cents,new_cents,actor,changed_at FROM price_audit ORDER BY id DESC LIMIT 25')] if role=='manager' else []}
    def report(self,start,end,role):
        self.role(role,'manager')
        try: a=date.fromisoformat(start); b=date.fromisoformat(end)
        except (TypeError,ValueError): raise ValidationError('Choose valid ISO dates.')
        if a>b: raise ValidationError('Start date must not be after end date.')
        try: finish=(b+timedelta(days=1)).isoformat()
        except OverflowError: raise ValidationError('End date is outside the supported range.')
        with self.connect() as c:
            rows={i['code']:{'code':i['code'],'name':i['name'],'unit':i['unit'],'quantity':D(0),'revenue_cents':0,'profit_cents':0} for i in self.items()}
            for r in c.execute('SELECT l.* FROM sale_lines l JOIN sales s ON l.serial=s.serial WHERE s.completed_at>=? AND s.completed_at<?',(a.isoformat(),finish)):
                row=rows[r['code']]; row['quantity']+=D(r['quantity']); row['revenue_cents']+=r['revenue_cents']; row['profit_cents']+=r['revenue_cents']-r['cost_cents']
            count=c.execute('SELECT COUNT(*) FROM sales WHERE completed_at>=? AND completed_at<?',(a.isoformat(),finish)).fetchone()[0]
            for row in rows.values(): row['quantity']=str(row['quantity'])
            return {'start':start,'end':end,'timezone':'UTC','rows':list(rows.values()),'revenue_cents':sum(r['revenue_cents'] for r in rows.values()),'profit_cents':sum(r['profit_cents'] for r in rows.values()),'sale_count':count}

class Handler(BaseHTTPRequestHandler):
    store=None
    def log_message(self,fmt,*args): pass
    def send_json(self,data,status=200):
        payload=json.dumps(data).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(payload))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(payload)
    def run_api(self,write=False):
        try:
            parsed=urlparse(self.path); path=parsed.path; role=self.headers.get('X-Demo-Role','')
            if write:
                # Reject cross-origin form submissions. Demo server is loopback-only, not authenticated.
                origin=self.headers.get('Origin')
                if origin and origin not in ('http://'+self.headers.get('Host',''),): raise PermissionError('Cross-origin requests are not allowed.')
                if self.headers.get_content_type()!='application/json': raise ValidationError('JSON content type required.')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=100000: raise ValidationError('Invalid request size.')
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict): raise ValidationError('Expected a JSON object.')
                actions={'/api/checkout':self.store.checkout,'/api/restock':self.store.restock,'/api/price':self.store.price}
                if path not in actions: return self.send_json({'error':'Not found'},404)
                return self.send_json(actions[path](data,role))
            if path=='/api/items': result=self.store.items()
            elif path=='/api/sales': self.store.role(role,'clerk'); result=self.store.sales()
            elif path.startswith('/api/sales/'): self.store.role(role,'clerk'); result=self.store.sale(path.split('/')[-1])
            elif path=='/api/audit': result=self.store.audit(role)
            elif path=='/api/report':
                q=parse_qs(parsed.query); result=self.store.report(q.get('start',[''])[0],q.get('end',[''])[0],role)
            else: return self.send_json({'error':'Not found'},404)
            self.send_json(result)
        except PermissionError as e: self.send_json({'error':str(e)},403)
        except (ValueError,TypeError,KeyError) as e: self.send_json({'error':str(e)},400)
        except sqlite3.Error: self.send_json({'error':'Database operation failed; no partial transaction was committed.'},500)
    def do_POST(self): self.run_api(True)
    def do_GET(self):
        if urlparse(self.path).path.startswith('/api/'): return self.run_api()
        path=urlparse(self.path).path
        files={'/':('index.html','text/html'),'/style.css':('style.css','text/css'),'/app.js':('app.js','application/javascript')}
        if path.startswith('/static/'): path=path[7:]
        if path not in files: return self.send_json({'error':'Not found'},404)
        name,mime=files[path]; payload=(ROOT/'static'/name).read_bytes()
        self.send_response(200); self.send_header('Content-Type',mime+'; charset=utf-8'); self.send_header('Content-Length',str(len(payload))); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(payload)

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--port',type=int,default=8000); parser.add_argument('--db',default=str(ROOT/'sas.db')); args=parser.parse_args()
    Handler.store=Store(args.db); server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'SAS demo: http://127.0.0.1:{args.port} | Database: {args.db}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: print('\nStopped.')
    finally: server.server_close()
if __name__=='__main__': main()
