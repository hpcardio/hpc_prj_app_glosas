/* All remote values enter the DOM through textContent or form values. */
(() => {
  'use strict';
  const root = document.getElementById('petrobras-followup');
  const dialog = document.getElementById('petrobras-item-dialog');
  if (!root || !dialog) return;
  if (root.dataset.convenio && !root.dataset.convenio.toUpperCase().includes('PETROBRAS')) { root.hidden = true; }
  const base = root.dataset.base.replace(/status$/, '');
  const get = role => root.querySelector(`[data-role="${role}"]`) || dialog.querySelector(`[data-role="${role}"]`);
  const readOnly = root.dataset.readonly === 'true';
  const token = root.querySelector('[name=csrfmiddlewaretoken]').value;
  const labels = {conciliado:'Conciliado',nao_localizado:'Não localizado no MV Sistema',ambiguo:'Correspondência ambígua',dados_insuficientes:'Dados insuficientes',rascunho:'Rascunho',protocolado:'Protocolado',em_analise:'Em análise',respondido:'Respondido',envio_incerto:'Envio não confirmado',transmitindo:'Transmissão iniciada'};
  let config = {}, busy = false, configLoaded = null;
  const mv = t => String(t ?? '').replace(/\bOracle\b/g, 'MV Sistema');
  function el(tag, text, parent, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; if (parent) parent.append(n); return n; }
  function notice(text, error=false) {
    text = mv(text);
    const target = dialog.open ? get('dialog-message') : get('message');
    target.textContent = text; target.className = error ? 'petrobras-warning' : '';
  }
  async function api(path, method='GET', body) {
    const res = await fetch(base+path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':token}, body:body === undefined ? undefined : JSON.stringify(body)});
    let data; try { data = await res.json(); } catch (_) { throw new Error('Resposta indisponível. Tente novamente.'); }
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Revise os campos informados.');
    return data;
  }
  async function action(fn) {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy','true'); notice('Processando…');
    try { await fn(); } catch (e) { notice(e.message || 'Falha na consulta.',true); }
    finally { busy = false; root.removeAttribute('aria-busy'); }
  }
  // Cards are server-rendered; a reload shows the updated record in place.
  const reload = message => { try { sessionStorage.setItem('petrobras-followup-message', message); } catch (_) { /* optional */ } window.location.reload(); };
  const money = v => v == null ? 'Não informado' : Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  function facts(parent, entries) { const dl = el('dl',null,parent); entries.forEach(([k,v])=>{el('dt',k,dl);el('dd',v == null || v === '' ? 'Não informado' : String(v),dl);}); }
  function button(parent, text, fn, disabled=false) { const b=el('button',text,parent,'btn btn-outline-primary');b.type='button';b.disabled=disabled;b.addEventListener('click',()=>action(fn));return b; }
  function input(parent, label, type, val='', max) {const l=el('label',label,parent);const n=el(type==='textarea'?'textarea':'input',null,l,'form-control');if(type!=='textarea')n.type=type;n.value=val||'';if(max)n.maxLength=max;return n;}
  function select(parent,label,options){const l=el('label',label,parent),s=el('select',null,l,'form-select');options.forEach(([v,t])=>{const o=el('option',t,s);o.value=v;});return s;}
  const documentTypes = ['Comprovante de elegibilidade','Laudo do profissional de saúde','Pedido do profissional de saúde','Relatório do profissional de saúde','Prescrição do profissional de saúde','Comprovante de comparecimento (assinatura)','Embalagem/rótulo de materiais ou medicamentos','Comprovante de entrega de medicamentos','Notas fiscais','Boletim anestésico','Selos hemoterápicos','Exames de imagem','Exames de análises clínicas','Termo de consentimento','Guia de cobrança de taxa','Autorização prévia','Relatório de Auditoria'].map((label,i)=>{const code=String(i+1).padStart(2,'0');return [code,`${code} · ${label}`];});
  const value = v => v == null || v === '' ? 'Não informado' : String(v);
  const dateBr = v => /^\d{4}-\d{2}-\d{2}/.test(v || '') ? v.slice(0,10).split('-').reverse().join('/') : value(v);
  function fields(parent, entries) { const grid = el('div',null,parent,'petrobras-fields'); entries.forEach(([k,v,wide])=>{const f=el('div',null,grid,'petrobras-field'+(wide?' petrobras-field--wide':''));el('span',k,f);el('strong',value(v),f);}); return grid; }
  function section(parent, title, cls='') { const s=el('section',null,parent,'petrobras-section '+cls); if (title) el('h3',title,s); return s; }
  function chip(parent, text, tone='') { return el('span',text,parent,'petrobras-chip'+(tone?' petrobras-chip--'+tone:'')); }
  // Payment demonstrativo glosa: informed per guide, without items or reasons.
  const byGuide = row => row.fonte.escopo === 'guia_pagamento';
  function header(box, row, eyebrow) {
    const top=el('div',null,box,'petrobras-item-head');
    const guide=byGuide(row), f=row.fonte;
    el('span',eyebrow,top,'petrobras-eyebrow');
    el('h3',guide?`Guia ${value(f.senha || row.guia_prestador)} · ${value(f.descricao)}`:`${value(row.tuss)} · ${value(row.oracle?.descricao || f.descricao)}`,top);
    const chips=el('div',null,top,'petrobras-chips');
    chip(chips,`Recurso: ${labels[row.recurso_estado] || row.recurso_estado}`);
    chip(chips,`MV Sistema: ${labels[row.cruzamento] || row.cruzamento}`, row.cruzamento==='conciliado'?'ok':'warn');
    if(row.acato)chip(chips,`Acato: ${money(row.acato.valor)}`,'ok');
    const strip=el('div',null,box,'petrobras-summary');
    (guide?[['Valor processado',money(f.valor_processado)],['Valor glosado',money(row.valor_glosa)],['Valor liberado',money(f.valor_liberado)],['Data de pagamento',dateBr(f.data_pagamento)]]
      :[['Valor informado',money(f.valor_informado)],['Valor glosado',money(row.valor_glosa)],['Valor liberado',money(f.valor_liberado)],['Quantidade',value(f.quantidade)],['Data realização',dateBr(f.data_realizacao)]])
      .forEach(([k,v],i)=>{const c=el('div',null,strip,'petrobras-summary-card'+(i===1?' petrobras-summary-card--danger':''));el('span',k,c);el('strong',v,c);});
  }
  function warnings(box, row) {
    const list=[];
    if(!row.presente_na_fonte)list.push('Não consta na última versão do demonstrativo; histórico preservado.');
    (row.fonte.avisos||[]).forEach(m=>list.push(m));
    if(row.erro_oracle)list.push(mv(row.erro_oracle));
    if(list.length){const w=el('div',null,box,'petrobras-alerts');list.forEach(m=>el('p',m,w));}
  }
  const digits = v => String(v ?? '').trim().replace(/^0+(?=.)/, '');
  const num = v => v == null || v === '' ? null : Number(v);
  // One value per field; the API key (carteira + senha + procedimento) and
  // the other comparable fields only flag a divergence.
  function details(box,row) {
    const guide=byGuide(row);
    const sec=section(box,guide?'Dados da guia':'Dados do item');
    const o=row.oracle||{}, f=row.fonte, has=v=>v!=null&&v!=='';
    const senha=f.senha || row.senha_guia;
    const pick=(a,b)=>has(a)?a:b;
    const entries=guide?[
      ['Paciente',o.nm_paciente,null,'wide'],
      ['Atendimento',o.cd_atendimento],
      ['Carteira',o.nr_carteira],
      ['Guia (senha)',pick(senha,o.nr_guia),[senha,o.nr_guia,(a,b)=>digits(a)===digits(b)]],
      ['Lote',f.lote],
      ['Remessa MV',o.cd_remessa],
      ['Data de atendimento',dateBr(o.dt_atendimento)],
      ['Itens no MV',o.itens_mv],
      ['Valor no MV',has(o.valor_mv)?money(o.valor_mv):null],
    ]:[
      ['Paciente',o.nm_paciente,null,'wide'],
      ['Atendimento',o.cd_atendimento],
      ['Carteira',pick(f.carteira,o.nr_carteira),[f.carteira,o.nr_carteira,(a,b)=>digits(a)===digits(b)]],
      ['Guia (senha)',pick(senha,o.nr_guia),[senha,o.nr_guia,(a,b)=>digits(a)===digits(b)]],
      ['Lote',f.lote],
      ['Cód. do procedimento',pick(row.tuss,o.cd_pro_fat),[row.tuss,o.cd_pro_fat,(a,b)=>digits(a)===digits(b)]],
      ['Data de realização',dateBr(pick(f.data_realizacao,o.data_realizacao)),[f.data_realizacao,o.data_realizacao,(a,b)=>String(a).slice(0,10)===String(b).slice(0,10)]],
      ['Procedimento',pick(o.descricao,f.descricao),null,'wide'],
      ['Quantidade',pick(f.quantidade,o.quantidade),[f.quantidade,o.quantidade,(a,b)=>num(a)===num(b)]],
      ['Valor',has(pick(f.valor_informado,o.valor))?money(pick(f.valor_informado,o.valor)):null,[f.valor_informado,o.valor,(a,b)=>num(a)===num(b)]],
    ];
    const grid=el('div',null,sec,'petrobras-datagrid');
    entries.forEach(([label,v,check,wide])=>{
      const c=el('div',null,grid,'petrobras-datum'+(wide?' petrobras-datum--wide':''));
      el('span',label,c);el('strong',value(v),c);
      if(check&&row.oracle&&has(check[0])&&has(check[1])&&!check[2](check[0],check[1])){c.classList.add('petrobras-datum--warn');chip(c,'Divergente','warn');}
    });
    if(!row.oracle)el('p',guide?`Sem vínculo confirmado no MV Sistema pela senha da guia. Atendimentos encontrados: ${row.candidatos}.`:`Sem vínculo confirmado no MV Sistema pela chave carteira + senha + procedimento. Candidatos: ${row.candidatos}.`,sec,'petrobras-muted');
    if((row.divergencias||[]).length){const n=el('ul',null,sec,'petrobras-notes');row.divergencias.forEach(d=>el('li',mv(d),n));}
    const tools=el('div',null,sec,'petrobras-actions petrobras-actions--compact');
    button(tools,'Conferir novamente no MV Sistema',async()=>{await api(`glosas/${row.id}/cruzar`,'POST',{});reload('Conferência atualizada.');},readOnly);
  }
  function reasons(box,row) {
    const sec=section(box,'Motivos da glosa');
    const list=row.motivos||row.fonte.motivos||[];
    if(!list.length){el('p',byGuide(row)?'A operadora não detalhou os motivos: a glosa foi informada por guia no demonstrativo de pagamento.':'Não informado',sec,'petrobras-muted');return;}
    const table=el('table',null,sec,'petrobras-compare petrobras-reasons-table');
    const head=el('tr',null,el('thead',null,table));['Código','Descrição','Valor'].forEach(t=>el('th',t,head));
    const body=el('tbody',null,table);
    list.forEach(m=>{const tr=el('tr',null,body);el('td',value(m.codigo),tr);el('td',value(m.descricao),tr);el('td',m.valor!=null?money(m.valor):'Não informado',tr);});
  }
  function render(row) {
    const box = get('dialog-body'); box.replaceChildren(); get('dialog-message').textContent = '';
    get('dialog-title').textContent = 'Recurso pela integração Petrobras';
    header(box,row,`Demonstrativo ${value(row.demonstrativo)} · Protocolo ${value(row.fonte.protocolo)}`);
    warnings(box,row);
    reasons(box,row);
    details(box,row);
    const editable=!readOnly&&row.recurso_estado==='rascunho';
    const edit=section(box,'Justificativa e prazo','petrobras-appeal petrobras-half');
    const justification=input(edit,'Justificativa','textarea',row.justificativa,500);justification.disabled=!editable;
    const line=el('div',null,edit,'petrobras-form-row');
    const due=input(line,'Prazo confirmado','date',row.prazo),origin=input(line,'Origem da regra do prazo','text',row.origem_prazo,500);due.disabled=origin.disabled=!editable;
    const saveRow=el('div',null,edit,'petrobras-actions petrobras-actions--end');
    button(saveRow,'Salvar rascunho',async()=>{await api(`glosas/${row.id}/rascunho`,'PUT',{versao:row.versao,justificativa:justification.value,prazo:due.value||null,origem_prazo:origin.value||null});reload('Rascunho salvo.');},!editable);
    const docs=section(box,'Comprovantes','petrobras-half');
    const list=el('div',null,docs,'petrobras-docs');
    const docTools=el('div',null,docs,'petrobras-actions');
    button(docTools,'Listar comprovantes',async()=>{list.replaceChildren();const files=await api(`glosas/${row.id}/anexos`);if(!files.length)el('p','Nenhum comprovante anexado.',list,'petrobras-muted');files.forEach(a=>{const p=el('p',null,list),link=el('a',a.nome,p);link.href=base+`glosas/${row.id}/anexos/${a.id}`;el('span',` · ${labels[a.estado]||a.estado}${a.protocolo?' · Protocolo '+a.protocolo:''}`,p);});notice('Comprovantes carregados.');});
    if(editable){
      const upload=el('div',null,docs,'petrobras-form-row petrobras-form-row--upload');const file=input(upload,'Comprovante (PDF, PNG ou JPEG)','file');file.accept='.pdf,.png,.jpg,.jpeg';
      const kind=select(upload,'Tipo do documento TISS',[['','Selecione'],...documentTypes]);
      const nature=select(upload,'Natureza da guia',[['','Selecione'],['1','Consulta'],['2','SP/SADT'],['3','Resumo de internação']]);
      const up=el('div',null,docs,'petrobras-actions petrobras-actions--end');
      button(up,'Anexar ao item',async()=>{const f=file.files[0];if(!f||!kind.value||!nature.value)throw new Error('Selecione arquivo, tipo de documento e natureza da guia.');if(f.size>config.limite_anexo_bytes)throw new Error('Arquivo acima do limite configurado.');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Não foi possível ler o arquivo.'));reader.readAsDataURL(f);});await api(`glosas/${row.id}/anexos`,'POST',{nome:f.name,conteudo_base64:data,tipo_documento:kind.value,natureza_guia:nature.value});reload('Comprovante anexado ao rascunho.');});
    }
    const sendSec=section(box,'Envio à Petrobras');
    fields(sendSec,[['Situação do recurso',labels[row.recurso_estado] || row.recurso_estado],['Protocolo do recurso',row.recurso_protocolo],['Prazo',row.prazo ? dateBr(row.prazo)+(row.origem_prazo?' · '+row.origem_prazo:'') : 'Não confirmado',true]]);
    if(row.recurso_resultado){const d=el('details',null,sendSec);el('summary','Resposta da operadora',d);el('pre',JSON.stringify(row.recurso_resultado,null,2),d);}
    const sendTools=el('div',null,sendSec,'petrobras-actions');
    button(sendTools,'Consultar recurso',async()=>{await api(`glosas/${row.id}/consultar-recurso`,'POST',{});reload('Situação consultada.');},readOnly||!row.recurso_protocolo);
    if(editable){
      const sendBox=el('details',null,sendSec);el('summary','Transmitir recurso',sendBox);el('p','Salve a justificativa e revise os comprovantes antes de transmitir. O envio usa o rascunho salvo.',sendBox,'petrobras-muted');
      const label=el('label',null,sendBox,'petrobras-check'),confirm=el('input',null,label);confirm.type='checkbox';el('span',' Confirmo o envio deste recurso à Petrobras.',label);
      button(sendBox,'Transmitir recurso salvo',async()=>{if(!confirm.checked)throw new Error('Marque a confirmação de envio.');await api(`glosas/${row.id}/enviar`,'POST',{versao:row.versao,confirmar_envio:true});reload('Consulte o protocolo apresentado no item.');},!config.transmissao_usuario||Boolean(row.bloqueio_envio));
      if(row.bloqueio_envio){el('p',row.bloqueio_envio,sendBox,'petrobras-warning');const portal=el('a','Abrir portal Petrobras',sendBox);portal.href='https://portaltiss.saudepetrobras.com.br/saudeweb/seguranca/login';portal.target='_blank';portal.rel='noopener noreferrer';}
      else if(!config.transmissao_habilitada)el('p','Transmissão ainda não habilitada neste ambiente.',sendBox,'petrobras-muted');
      else if(!config.transmissao_usuario)el('p','Envio de recurso em piloto; seu usuário ainda não foi liberado.',sendBox,'petrobras-muted');
    }
    if(['envio_incerto','transmitindo'].includes(row.recurso_estado)){el('p','Confira o protocolo no portal antes de qualquer nova tentativa.',sendSec,'petrobras-warning');const link=el('a','Abrir portal Petrobras',sendSec);link.href='https://portaltiss.saudepetrobras.com.br/saudeweb/seguranca/login';link.target='_blank';link.rel='noopener noreferrer';}
    history(box,row);
  }
  function history(box,row){
    const h=el('details',null,box,'petrobras-history');el('summary','Histórico de alterações',h);
    h.addEventListener('toggle',()=>{if(h.open&&!h.dataset.loaded)action(async()=>{const events=await api(`glosas/${row.id}/historico`);events.forEach(e=>{el('p',`${new Date(e.data).toLocaleString('pt-BR')} · ${e.tipo}`,h);el('pre',JSON.stringify(e.dados,null,2),h);});h.dataset.loaded='true';notice('Histórico carregado.');});});
  }
  // Internal acato, as for payers without integration: never sent to Petrobras.
  function renderAcato(row) {
    const box = get('dialog-body'); box.replaceChildren(); get('dialog-message').textContent = '';
    get('dialog-title').textContent = 'Registro de acato';
    header(box,row,`Controle interno · Demonstrativo ${value(row.demonstrativo)} · Protocolo ${value(row.fonte.protocolo)}`);
    el('p','O acato fica registrado apenas no Receita Certa; nada é enviado à Petrobras.',box,'petrobras-muted');
    const glosa=section(box,'Processo original','petrobras-half');
    fields(glosa,[['Paciente',row.oracle?.nm_paciente],['Atendimento',row.oracle?.cd_atendimento],['Conta',row.oracle?.cd_reg],['Motivo da glosa',(row.motivos||row.fonte.motivos||[]).map(m=>m.descricao?`${m.codigo} - ${m.descricao}`:m.codigo).join('; '),true]]);
    const a=row.acato||{}, editable=!readOnly&&row.recurso_estado==='rascunho';
    const form=section(box,'Tratamento por acato','petrobras-half');
    const r1=el('div',null,form,'petrobras-form-row');
    const lote=input(r1,'Lote','text',a.lote||row.fonte.lote,255),data=input(r1,'Data do acato *','date',a.data||new Date().toISOString().slice(0,10)),proc=input(r1,'Processo','text',a.processo,255);
    const r2=el('div',null,form,'petrobras-form-row');
    const qtd=input(r2,'Qtd acatada *','number',a.quantidade||row.fonte.quantidade);qtd.min='0.01';qtd.step='0.01';if(row.fonte.quantidade)qtd.max=row.fonte.quantidade;
    const val=input(r2,'Valor acatado (R$) *','number',a.valor||row.valor_glosa);val.min='0.01';val.step='0.01';val.max=row.valor_glosa;
    const desc=input(form,'Descrição','textarea',a.descricao,2000);
    [lote,data,proc,qtd,val,desc].forEach(n=>{n.disabled=!editable;});
    if(row.recurso_estado!=='rascunho')el('p','Recurso já transmitido à Petrobras; acato bloqueado.',form,'petrobras-warning');
    const foot=el('div',null,box,'petrobras-actions petrobras-actions--end');
    if(row.acato)button(foot,'Excluir acato',async()=>{await api(`glosas/${row.id}/acato`,'DELETE');reload('Acato excluído.');},!editable).classList.add('petrobras-btn-danger');
    const cancel=el('button','Cancelar',foot,'btn btn-outline-primary');cancel.type='button';cancel.addEventListener('click',()=>dialog.close());
    button(foot,row.acato?'Salvar alterações':'Registrar acato',async()=>{if(!data.value||!qtd.value||!val.value)throw new Error('Informe data, quantidade e valor acatados.');await api(`glosas/${row.id}/acato`,'PUT',{data:data.value,quantidade:qtd.value,valor:Number(val.value).toFixed(2),lote:lote.value||null,processo:proc.value||null,descricao:desc.value});reload('Acato registrado no Follow-Up de Glosas.');},!editable).classList.add('petrobras-btn-success');
    history(box,row);
  }
  async function loadConfig() { configLoaded = configLoaded || api('status').then(data => { config = data; }); return configLoaded; }
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-petrobras-item],[data-petrobras-acato]');
    if (!trigger) return;
    const acato = trigger.hasAttribute('data-petrobras-acato');
    let row;
    try { row = JSON.parse(acato ? trigger.dataset.petrobrasAcato : trigger.dataset.petrobrasItem); } catch (_) { return; }
    action(async () => { await loadConfig(); (acato ? renderAcato : render)(row); if (!dialog.open) dialog.showModal(); notice(''); });
  });
  dialog.querySelector('[data-dialog-close]').addEventListener('click', () => dialog.close());
  async function protocols(){const rows=await api('protocolos');const s=get('protocol'),selected=s.value;s.replaceChildren();el('option','Selecione',s).value='';rows.forEach(r=>{const o=el('option',r.numero,s);o.value=r.id;});s.value=selected;}
  async function runs(){const rows=await api('execucoes');get('runs').replaceChildren();rows.slice(0,5).forEach(r=>el('p',`${new Date(r.inicio).toLocaleString('pt-BR')} · ${r.situacao}${r.erro?' · '+r.erro:''}`,get('runs')));}
  const actions={
    discover:async()=>{const competencia=get('competencia').value.replace('-','');if(!competencia)throw new Error('Selecione a competência.');const r=await api('competencias/sincronizar','POST',{competencia});reload(`${r.protocolos_localizados} protocolos localizados; ${r.quantidade || 0} registros importados; ${r.glosas_por_guia || 0} glosas por guia. ${(r.avisos || []).join(' ')}`);},
    sync:async()=>{const id=get('protocol').value;if(!id)throw new Error('Selecione um protocolo.');const r=await api(`protocolos/${id}/sincronizar`,'POST',{});reload(`${r.quantidade || 0} registros importados. ${(r.avisos || []).join(' ')}`);},
    'add-protocol':async()=>{const numero=get('new-protocol').value.trim();await api('protocolos','POST',{numero});await protocols();notice('Protocolo adicionado.');},
    'match-all':async()=>{const r=await api('glosas/cruzar','POST',{});reload(`${r.conferidos} registros conferidos no MV Sistema; ${r.alterados} atualizados.`);},
    'portal-detail':async()=>{notice('Consultando o portal Saúde Petrobras...');const r=await api('portal/detalhar','POST',{});const falhas=Object.entries(r.falhas||{}).map(([l,m])=>`lote ${l}: ${m}`).join('; ');reload(r.lotes?`${r.lotes_detalhados} de ${r.lotes} lotes detalhados; ${r.quantidade||0} itens importados.${falhas?' Pendentes: '+falhas:''}`:'Nenhuma glosa por guia pendente de detalhamento.');},
  };
  root.querySelectorAll('[data-action]').forEach(b=>{b.addEventListener('click',()=>action(actions[b.dataset.action]));if(readOnly)b.disabled=true;});
  // Load protocol options only when the operator opens the controls.
  root.querySelector('[data-role="controls"]').addEventListener('toggle', event => {
    if (event.target.open && !root.dataset.loaded) { root.dataset.loaded = 'true'; action(async()=>{await loadConfig();await protocols();await runs();notice(config.transmissao_habilitada ? 'Integração disponível.' : 'Integração disponível; transmissão ainda não habilitada.');}); }
  });
  let pending = null;
  try { pending = sessionStorage.getItem('petrobras-followup-message'); sessionStorage.removeItem('petrobras-followup-message'); } catch (_) { pending = null; }
  if (pending) { root.querySelector('[data-role="controls"]').open = true; notice(pending); }
})();
