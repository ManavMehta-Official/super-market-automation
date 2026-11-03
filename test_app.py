"""Repeatable stdlib unit, transaction, concurrency and HTTP tests; never touches sas.db."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import http.client
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from app import Store, Handler, ValidationError, decimal, money, quantity, cents


class Numbers(unittest.TestCase):
    def test_decimal_valid(self):
        self.assertEqual(decimal('0.125'), Decimal('.125'))
    def test_decimal_invalid(self):
        for value in ['NaN', 'Infinity', '-Infinity', 'x', None, True, '100000001', '1e1000000']:
            with self.subTest(value=value), self.assertRaises(ValidationError): decimal(value)
    def test_money_exact(self):
        self.assertEqual(money('12.34'), 1234)
        self.assertEqual(money('0'), 0)
    def test_money_rejects_negative_and_extra_places(self):
        for value in ['-0.01', '1.001', '1.000']:
            with self.subTest(value=value), self.assertRaises(ValidationError): money(value)
    def test_quantity_precision(self):
        self.assertEqual(quantity('0.001','kg'), Decimal('.001'))
        self.assertEqual(quantity('2.0','each'), Decimal(2))
    def test_quantity_invalid(self):
        for value,unit in [('0','kg'),('-1','each'),('1.5','each'),('.0001','kg'),('1','litre')]:
            with self.subTest(value=value,unit=unit), self.assertRaises(ValidationError): quantity(value,unit)
    def test_round_half_up(self):
        self.assertEqual(cents(Decimal('12.5')),13)
        self.assertEqual(cents(Decimal('12.49')),12)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/'test.db'
        self.store=Store(self.db)
    def tearDown(self): self.tmp.cleanup()
    def item(self,code='MILK'):
        return next(i for i in self.store.items() if i['code']==code)
    def sale_data(self,key='test',code='MILK',q='1'):
        i=self.item(code)
        return {'request_id':key,'lines':[{'code':code,'quantity':q,'unit':i['unit'],'price_cents':i['price_cents'],'stable':True}]}
    def restock_data(self,ref='delivery',q='8',cost='60.00'):
        return {'reference':ref,'code':'MILK','quantity':q,'unit':'each','cost':cost}
    def report(self): return self.store.report('2000-01-01','2099-12-31','manager')
    def test_seed_six_items_no_sales(self):
        self.assertEqual(len(self.store.items()),6)
        self.assertEqual(self.store.sales(),[])
        self.assertEqual(self.report()['sale_count'],0)
    def test_checkout_receipt_and_stock(self):
        sale=self.store.checkout(self.sale_data(q='2'),'clerk')
        self.assertEqual(sale['total_cents'],12400)
        self.assertEqual(sale['lines'][0]['cost_cents'],9600)
        self.assertEqual(Decimal(self.item()['stock']),40)
        self.assertTrue(sale['serial'].startswith('SAS-'))
        self.assertEqual(datetime.fromisoformat(sale['completed_at']).utcoffset().total_seconds(),0)
    def test_empty_checkout(self):
        for lines in [[], None, 'bad', [None], [{}]]:
            with self.subTest(lines=lines), self.assertRaises(ValidationError):
                self.store.checkout({'request_id':'empty','lines':lines},'clerk')
        self.assertEqual(self.store.sales(),[])
    def test_request_id_required(self):
        for key in ['',None,123,'a'*129]:
            with self.subTest(key=key), self.assertRaises(ValidationError):
                self.store.checkout(self.sale_data(key=key),'clerk')
    def test_idempotent_sale_exact_payload(self):
        data=self.sale_data(q='2')
        first=self.store.checkout(data,'clerk')
        self.assertEqual(first,self.store.checkout(copy.deepcopy(data),'clerk'))
        self.assertEqual(len(self.store.sales()),1)
        self.assertEqual(Decimal(self.item()['stock']),40)
    def test_request_collision_rejected(self):
        self.store.checkout(self.sale_data(),'clerk')
        with self.assertRaises(ValidationError): self.store.checkout(self.sale_data(q='2'),'clerk')
        self.assertEqual(Decimal(self.item()['stock']),41)
    def test_aggregate_duplicate_lines_stock(self):
        data=self.sale_data(q='22'); data['lines']*=2
        with self.assertRaises(ValidationError): self.store.checkout(data,'clerk')
        self.assertEqual(Decimal(self.item()['stock']),42)
        self.assertEqual(self.store.sales(),[])
    def test_duplicate_lines_success(self):
        data=self.sale_data(q='2'); data['lines']*=2
        sale=self.store.checkout(data,'clerk')
        self.assertEqual(len(sale['lines']),2)
        self.assertEqual(Decimal(self.item()['stock']),38)
    def test_stock_failure_atomic_multiple_items(self):
        data=self.sale_data(); data['lines']+=self.sale_data(code='BREAD',q='31')['lines']
        with self.assertRaises(ValidationError): self.store.checkout(data,'clerk')
        self.assertEqual(Decimal(self.item()['stock']),42)
        self.assertEqual(Decimal(self.item('BREAD')['stock']),30)
        self.assertEqual(self.store.sales(),[])
    def test_database_failure_rolls_back_inserted_sale(self):
        with self.store.connect() as c:
            c.executescript("CREATE TRIGGER fail_stock BEFORE UPDATE OF stock ON items BEGIN SELECT RAISE(ABORT,'injected failure'); END;")
        with self.assertRaises(sqlite3.IntegrityError): self.store.checkout(self.sale_data(),'clerk')
        self.assertEqual(self.store.sales(),[])
        with self.store.connect() as c: self.assertEqual(c.execute('SELECT COUNT(*) FROM sale_lines').fetchone()[0],0)
        self.assertEqual(Decimal(self.item()['stock']),42)
    def test_weight_rounding_and_stable_required(self):
        data=self.sale_data(code='RICE',q='.001')
        sale=self.store.checkout(data,'clerk')
        self.assertEqual(sale['total_cents'],13)
        self.assertEqual(sale['lines'][0]['cost_cents'],9)
        self.assertEqual(Decimal(self.item('RICE')['stock']),Decimal('75.499'))
        for stable in [False,None,1,'true']:
            data=self.sale_data(key=str(stable),code='RICE'); data['lines'][0]['stable']=stable
            with self.subTest(stable=stable), self.assertRaises(ValidationError): self.store.checkout(data,'clerk')
    def test_unit_and_quantity_invalid(self):
        for field,value in [('unit','kg'),('quantity','1.1'),('quantity','-1'),('price_cents',True),('price_cents','6200')]:
            data=self.sale_data(); data['lines'][0][field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValidationError): self.store.checkout(data,'clerk')
    def test_roles_denied(self):
        for role in ['', 'manager','employee']:
            with self.subTest(role=role), self.assertRaises(PermissionError): self.store.checkout(self.sale_data(),role)
        with self.assertRaises(PermissionError): self.store.restock(self.restock_data(),'clerk')
        with self.assertRaises(PermissionError): self.store.price({'code':'MILK','price':'1','version':1},'employee')
        with self.assertRaises(PermissionError): self.store.report('2020-01-01','2030-01-01','clerk')
    def test_restock_weighted_cost_and_audit(self):
        item=self.store.restock(self.restock_data(),'employee')
        self.assertEqual(Decimal(item['stock']),50)
        self.assertEqual(item['avg_cost'],'49.920000')
        with self.store.connect() as c: self.assertEqual(c.execute('SELECT actor FROM receipts').fetchone()[0],'demo-employee')
    def test_restock_idempotency_and_collision(self):
        data=self.restock_data(); self.store.restock(data,'employee'); self.store.restock(data,'employee')
        self.assertEqual(Decimal(self.item()['stock']),50)
        data['cost']='61.00'
        with self.assertRaises(ValidationError): self.store.restock(data,'employee')
        self.assertEqual(Decimal(self.item()['stock']),50)
    def test_restock_invalid(self):
        for field,value in [('unit','kg'),('quantity','0'),('quantity','.5'),('cost','-1'),('reference',' '),('cost','.001')]:
            data=self.restock_data(); data[field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValidationError): self.store.restock(data,'employee')
        self.assertEqual(Decimal(self.item()['stock']),42)
    def test_restock_zero_stock_uses_new_cost(self):
        self.store.checkout(self.sale_data(q='42'),'clerk')
        self.assertEqual(self.store.restock(self.restock_data(),'employee')['avg_cost'],'60.000000')
    def test_price_optimistic_version_audit(self):
        data={'code':'MILK','price':'65.00','version':1}
        item=self.store.price(data,'manager')
        self.assertEqual(item['price_cents'],6500); self.assertEqual(item['version'],2)
        with self.assertRaises(ValidationError): self.store.price(data,'manager')
        with self.store.connect() as c:
            audit=dict(c.execute('SELECT * FROM price_audit').fetchone())
        self.assertEqual((audit['old_cents'],audit['new_cents'],audit['actor']),(6200,6500,'demo-manager'))
    def test_changed_price_requires_refresh(self):
        data=self.sale_data()
        self.store.price({'code':'MILK','price':'65','version':1},'manager')
        with self.assertRaises(ValidationError): self.store.checkout(data,'clerk')
        self.assertEqual(self.store.sales(),[])
    def test_historical_profit_immutable_after_cost_and_price(self):
        receipt=self.store.checkout(self.sale_data(q='2'),'clerk'); report=self.report()
        self.store.price({'code':'MILK','price':'70','version':1},'manager')
        self.store.restock(self.restock_data(cost='100'),'employee')
        self.assertEqual(self.store.sale(receipt['serial']),receipt)
        self.assertEqual(self.report(),report)
        self.assertEqual(report['profit_cents'],2800)
    def test_report_inclusive_utc_dates_and_zero_items(self):
        sales=[self.store.checkout(self.sale_data(key=str(i)),'clerk') for i in range(3)]
        with self.store.connect() as c:
            for sale,stamp in zip(sales,['2026-01-01T00:00:00+00:00','2026-01-01T23:59:59+00:00','2026-01-02T00:00:00+00:00']):
                c.execute('UPDATE sales SET completed_at=? WHERE serial=?',(stamp,sale['serial']))
        report=self.store.report('2026-01-01','2026-01-01','manager')
        self.assertEqual(report['sale_count'],2); self.assertEqual(report['revenue_cents'],12400)
        self.assertEqual(len(report['rows']),6); self.assertEqual(report['timezone'],'UTC')
        self.assertEqual(sum(r['quantity']=='0' for r in report['rows']),5)
    def test_audit_role_filtering(self):
        self.store.restock(self.restock_data(),'employee')
        self.store.price({'code':'MILK','price':'65','version':1},'manager')
        with self.assertRaises(PermissionError): self.store.audit('clerk')
        employee=self.store.audit('employee'); manager=self.store.audit('manager')
        self.assertEqual(len(employee['receipts']),1); self.assertEqual(employee['prices'],[])
        self.assertEqual(len(manager['prices']),1)
        self.assertEqual(manager['prices'][0]['actor'],'demo-manager')
    def test_report_invalid_ranges(self):
        for start,end in [('bad','2026-01-01'),('2026-02-30','2026-03-01'),('2026-02-01','2026-01-01'),('2026-01-01','9999-12-31')]:
            with self.subTest(start=start,end=end), self.assertRaises(ValidationError): self.store.report(start,end,'manager')
    def test_persistence_recreate_store_no_reseed(self):
        sale=self.store.checkout(self.sale_data(q='42'),'clerk')
        again=Store(self.db)
        self.assertEqual(again.sale(sale['serial']),sale)
        self.assertEqual(next(i['stock'] for i in again.items() if i['code']=='MILK'),'0')
    def test_concurrent_checkout_no_oversell(self):
        barrier=threading.Barrier(2)
        def buy(i):
            data=self.sale_data(key=str(i),q='30'); barrier.wait()
            try: return self.store.checkout(data,'clerk')
            except ValidationError: return None
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(buy,range(2)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(Decimal(self.item()['stock']),12)
    def test_concurrent_same_id_exactly_once(self):
        data=self.sale_data(q='2'); barrier=threading.Barrier(4)
        def buy(_): barrier.wait(); return self.store.checkout(data,'clerk')
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(buy,range(4)))
        self.assertEqual(len({r['serial'] for r in results}),1)
        self.assertEqual(Decimal(self.item()['stock']),40)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.tmp.name)/'http.db')
        class TestHandler(Handler): pass
        TestHandler.store=self.store
        self.server=ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()
    def request(self,path,method='GET',data=None,role='',raw=None,extra=None):
        con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'X-Demo-Role':role,'Content-Type':'application/json'}; headers.update(extra or {})
        body=raw if raw is not None else (json.dumps(data) if data is not None else None)
        con.request(method,path,body,headers); response=con.getresponse(); status=response.status
        content=response.read(); con.close()
        return status,json.loads(content)
    def test_items_and_unknown_endpoint(self):
        status,data=self.request('/api/items'); self.assertEqual(status,200); self.assertEqual(len(data),6)
        self.assertEqual(self.request('/api/missing')[0],404)
        self.assertEqual(self.request('/missing')[0],404)
        self.assertEqual(self.request('/api/missing','POST',{})[0],404)
    def test_forbidden_roles(self):
        for path,method,data in [('/api/sales','GET',None),('/api/report?start=2026-01-01&end=2026-12-31','GET',None),('/api/checkout','POST',{}),('/api/restock','POST',{}),('/api/price','POST',{})]:
            with self.subTest(path=path): self.assertEqual(self.request(path,method,data)[0],403)
    def test_malformed_payload_and_content_type(self):
        for raw in ['{','[]','null','"string"','']:
            with self.subTest(raw=raw): self.assertEqual(self.request('/api/checkout','POST',role='clerk',raw=raw)[0],400)
        self.assertEqual(self.request('/api/checkout','POST',{},'clerk',extra={'Content-Type':'text/plain'})[0],400)
    def test_extreme_decimal_returns_validation_error(self):
        data={'request_id':'extreme','lines':[{'code':'MILK','quantity':'1e1000000','unit':'each','price_cents':6200}]}
        self.assertEqual(self.request('/api/checkout','POST',data,'clerk')[0],400)
        self.assertEqual(self.store.sales(),[])
    def test_cross_origin_rejected(self):
        self.assertEqual(self.request('/api/checkout','POST',{},'clerk',extra={'Origin':'https://evil.example'})[0],403)
    def test_audit_endpoint_roles(self):
        self.assertEqual(self.request('/api/audit',role='clerk')[0],403)
        self.assertEqual(self.request('/api/audit',role='employee'),(200,{'receipts':[],'prices':[]}))
        self.assertEqual(self.request('/api/audit',role='manager')[0],200)
    def test_static_assets(self):
        for path in ['/', '/static/style.css', '/static/app.js']:
            with self.subTest(path=path):
                con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
                con.request('GET',path); response=con.getresponse()
                self.assertEqual(response.status,200); self.assertGreater(len(response.read()),100)
                con.close()
    def test_complete_http_workflow(self):
        data={'request_id':'http-sale','lines':[{'code':'MILK','quantity':'1','unit':'each','price_cents':6200}]}
        status,sale=self.request('/api/checkout','POST',data,'clerk'); self.assertEqual(status,200)
        self.assertEqual(self.request('/api/sales/'+sale['serial'],role='clerk'),(200,sale))
        status,sales=self.request('/api/sales',role='clerk'); self.assertEqual(status,200); self.assertEqual(len(sales),1)
        self.assertEqual(self.request('/api/sales/missing',role='clerk')[0],400)
        status,report=self.request('/api/report?start=2000-01-01&end=2099-12-31',role='manager')
        self.assertEqual(status,200); self.assertEqual(report['profit_cents'],1400)
        status,item=self.request('/api/restock','POST',{'reference':'http-restock','code':'MILK','quantity':'1','unit':'each','cost':'55'},'employee')
        self.assertEqual(status,200); self.assertEqual(Decimal(item['stock']),42)
        status,item=self.request('/api/price','POST',{'code':'MILK','price':'65','version':1},'manager')
        self.assertEqual(status,200); self.assertEqual(item['price_cents'],6500)

if __name__=='__main__': unittest.main(verbosity=2)
