"""Local browser regression with mocked API; no production calls.

Install Playwright and Chromium in a local test environment, then run:
    python scripts/check_petrobras_hierarchy.py
Optional PLAYWRIGHT_CHROMIUM_EXECUTABLE selects an existing Chromium.
"""
import os,sys,json,threading

from pathlib import Path
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
os.chdir(Path(__file__).resolve().parents[1] / 'frontend')
sys.path.insert(0,os.getcwd());os.environ['DJANGO_SETTINGS_MODULE']='glosas_frontend.settings'
import django;django.setup()
from django.template.loader import render_to_string
from django.test import RequestFactory
from playwright.sync_api import sync_playwright
req=RequestFactory().get('/');req.session={'api_user':{'perfil':'usuario'}}
html=render_to_string('follow_up_glosas.html',{'request':req,'petrobras_enabled':True,'filtros':{'convenio':'SAUDE PETROBRAS'},'convenios':['SAUDE PETROBRAS'],'resumo':{},'pagination':{},'processos':[]},request=req)

class Handler(SimpleHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  if self.path=='/':self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(html.encode())
  else:super().do_GET()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
source={'protocolo':'123','escopo':'item','descricao':'Procedimento de teste','data_realizacao':'2026-10-01','quantidade':'1','valor_informado':'100.00','carteira':'00000001','motivos':[{'codigo':'1701','valor':'20.00'}]}
row={'id':1,'guia_prestador':'P001','guia_operadora':'000123','tuss':'00123456','demonstrativo':'D1','fonte':source,'valor_glosa':'20.00','presente_na_fonte':True,'cruzamento':'conciliado','oracle':{'cd_reg':10,'cd_lancamento':1,'cd_atendimento':101,'nr_guia':'000123','cd_tuss':'00123456','nr_carteira':'00000001','data_realizacao':'2026-10-01','quantidade':'1','valor':'100.00'},'candidatos':1,'divergencias':[],'recurso_estado':'rascunho','justificativa':'','versao':1,'prazo':None,'origem_prazo':None,'atualizado_em':'2026-10-29T15:00:00Z'}
source.update(cobertura='parcial_nao_confirmada', tabela='22', avisos=['Código de glosa fora do domínio TISS 4.01: 3292. Revisão necessária.'])

from copy import deepcopy
from urllib.parse import urlparse,parse_qs
rows=[deepcopy(row) for _ in range(4)]
for i,r in enumerate(rows):r['id']=i+1
rows[1]['tuss']='99999999'
rows[2]['guia_prestador']='P002';rows[2]['fonte']['carteira']='00000002';rows[2]['oracle']=None;rows[2]['cruzamento']='nao_localizado'
rows[3]['fonte']['protocolo']='456';rows[3]['oracle']=None;rows[3]['cruzamento']='nao_localizado'
import tempfile
output=Path(tempfile.mkdtemp(prefix='petrobras-hierarchy-'))
errors=[];calls=[]
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'))
 page=browser.new_page(viewport={'width':1600,'height':1000})
 page.on('pageerror',lambda e:errors.append(str(e)))
 def route(r):
  path=r.request.url.split('/petrobras/')[1].split('?')[0];calls.append(path)
  if path=='status':data={'habilitado':True,'transmissao_habilitada':False,'mapeamento_guia':'guia_prestador','limite_anexo_bytes':5242880}
  elif path=='glosas':
   assert all(v[0] for v in parse_qs(urlparse(r.request.url).query,keep_blank_values=True).values())
   data={'total':4,'items':rows}
  elif path=='protocolos':data=[{'id':1,'numero':'123'}]
  elif path=='execucoes':data=[]
  elif path.endswith('/rascunho'):
   rows[0].update(json.loads(r.request.post_data));rows[0]['versao']+=1;data=rows[0]
  else:data=[]
  r.fulfill(status=200,content_type='application/json',body=json.dumps(data))
 page.route('**/follow-up-glosas/petrobras/**',route)
 page.goto(f'http://127.0.0.1:{server.server_port}/')
 page.wait_for_function("document.querySelector('[data-role=message]').textContent==='Dados Petrobras carregados.'")
 assert page.locator('.filter-panel #petrobras-results').count()==0
 assert page.locator('#petrobras-results .petrobras-group[data-level="0"]').count()==1
 assert page.locator('#petrobras-results .petrobras-group[data-level="1"]').count()==2
 assert page.locator('#petrobras-results .petrobras-group[data-level="2"]').count()==3
 assert page.locator('.petrobras-card').count()==4
 assert page.locator('[data-role=totals]').inner_text().replace(chr(160),' ').count('R$ 80,00')==1
 assert not page.locator('.petrobras-card').first.is_visible()
 expand=page.locator('#petrobras-results [data-action=expand]')
 expand.click();expand.click()
 assert page.locator('.petrobras-group[data-level="2"]').first.is_visible()
 page.screenshot(path=str(output/'desktop.png'),full_page=True)
 expand.click()
 record=page.locator('.petrobras-card').first
 record.locator(':scope > summary').click()
 record.get_by_text('Justificativa e comprovantes',exact=True).click()
 record.get_by_label('Justificativa',exact=True).fill('Teste preservado')
 record.get_by_role('button',name='Salvar rascunho',exact=True).click()
 page.get_by_text('Rascunho salvo.',exact=True).wait_for()
 assert rows[0]['justificativa']=='Teste preservado'
 assert page.locator('.petrobras-card-body').first.is_visible()
 page.locator('#petrobras-results [data-action=collapse]').click()
 assert page.locator('#petrobras-results details[open]').count()==0
 assert page.locator('[data-role=totals]').inner_text().replace(chr(160),' ').count('R$ 80,00')==1
 first=page.locator('.petrobras-group[data-level="0"] > summary')
 first.focus();page.keyboard.press('Enter')
 assert page.locator('.petrobras-group[data-level="1"]').first.is_visible()
 page.set_viewport_size({'width':390,'height':844});page.evaluate("document.body.classList.add('sidebar-collapsed')")
 expand.click();expand.click();expand.click()
 assert page.locator('#petrobras-results').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
 page.locator('#petrobras-results').scroll_into_view_if_needed()
 assert page.locator('.content').evaluate('(el)=>getComputedStyle(el).overflowY')=='auto'
 page.screenshot(path=str(output/'mobile.png'))
 page.route('**/other',lambda r:r.fulfill(status=200,content_type='text/html',body=html.replace('data-convenio="SAUDE PETROBRAS"','data-convenio="ISSEC"')))
 before=len(calls);page.goto(f'http://127.0.0.1:{server.server_port}/other')
 assert page.locator('#petrobras-followup').is_hidden() and page.locator('#petrobras-results').is_hidden()
 assert len(calls)==before
 assert not errors,errors
 assert not any(p.endswith('/enviar') for p in calls)
 print('Screenshots:',output)
 print('PASS: hierarchy 1/2/3/4; exact page total; bulk and keyboard toggles; draft actions; mobile; other payer isolation')
 browser.close()
server.shutdown()
