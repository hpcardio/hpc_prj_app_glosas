/* All remote values enter the DOM through textContent or form values. */
(() => {
  'use strict';
  const root = document.getElementById('petrobras-followup');
  if (!root) return;
  if (root.dataset.convenio && !root.dataset.convenio.toUpperCase().includes('PETROBRAS')) { root.hidden = true; return; }
  const base = root.dataset.base.replace(/status$/, '');
  const get = role => root.querySelector(`[data-role="${role}"]`);
  const controls = get('controls');
  let keyboardNavigation = false;
  document.addEventListener('keydown', event => {
    if (event.key === 'Tab') keyboardNavigation = true;
  });
  document.addEventListener('pointerdown', () => { keyboardNavigation = false; });
  controls.addEventListener('pointerleave', event => {
    const keyboardFocus = keyboardNavigation && controls.contains(document.activeElement);
    if (event.pointerType === 'mouse' && !keyboardFocus) {
      controls.open = false;
    }
  });
  const readOnly = root.dataset.readonly === 'true';
  const token = root.querySelector('[name=csrfmiddlewaretoken]').value;
  const labels = {conciliado:'Conciliado',nao_localizado:'Não localizado no Oracle',ambiguo:'Correspondência ambígua',dados_insuficientes:'Dados insuficientes',rascunho:'Rascunho',protocolado:'Protocolado',em_analise:'Em análise',respondido:'Respondido',envio_incerto:'Envio não confirmado',transmitindo:'Transmissão iniciada'};
  let config = {}, offset = 0, total = 0, busy = false;
  function el(tag, text, parent, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; if (parent) parent.append(n); return n; }
  function notice(text, error=false) { get('message').textContent = text; get('message').className = error ? 'petrobras-warning' : ''; }
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
  const money = v => v == null ? 'Não informado' : Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  function facts(parent, entries) { const dl = el('dl',null,parent); entries.forEach(([k,v])=>{el('dt',k,dl);el('dd',v == null || v === '' ? 'Não informado' : String(v),dl);}); }
  function button(parent, text, fn, disabled=false) { const b=el('button',text,parent,'btn btn-outline-primary');b.type='button';b.disabled=disabled;b.addEventListener('click',()=>action(fn));return b; }
  function input(parent, label, type, val='', max) {const l=el('label',label,parent);const n=el(type==='textarea'?'textarea':'input',null,l,'form-control');if(type!=='textarea')n.type=type;n.value=val||'';if(max)n.maxLength=max;return n;}
  function select(parent,label,options){const l=el('label',label,parent),s=el('select',null,l,'form-select');options.forEach(([v,t])=>{const o=el('option',t,s);o.value=v;});return s;}
  function card(row) {
    const box=el('article',null,get('cards'),'petrobras-card');
    el('h3',`Guia ${row.guia_prestador || row.guia_operadora || 'não discriminada'} · ${row.tuss || 'Glosa de '+row.fonte.escopo}`,box);
    el('p',`${labels[row.cruzamento] || row.cruzamento} · Recurso: ${labels[row.recurso_estado] || row.recurso_estado}`,box);
    if(!row.presente_na_fonte)el('p','Não consta na última versão do demonstrativo; histórico preservado.',box,'petrobras-warning');
    if(row.fonte.cobertura==='parcial_nao_confirmada')el('p','Retorno parcial da operadora. Os totais gerais podem incluir outras guias.',box,'petrobras-warning');
    (row.fonte.avisos||[]).forEach(message=>el('p',message,box,'petrobras-warning'));
    if(row.erro_oracle)el('p',row.erro_oracle,box,'petrobras-warning');
    const compare=el('div',null,box,'petrobras-comparison'),src=el('div',null,compare),dst=el('div',null,compare);
    el('strong','Petrobras',src);
    facts(src,[['Demonstrativo',row.demonstrativo],['Protocolo',row.fonte.protocolo],['Guia prestador',row.guia_prestador],['Guia operadora',row.guia_operadora],['Procedimento',row.fonte.descricao],['Carteira',row.fonte.carteira],['Código do procedimento',row.tuss],['Tabela',row.fonte.tabela],['Data realização',row.fonte.data_realizacao],['Quantidade',row.fonte.quantidade],['Valor informado',money(row.fonte.valor_informado)],['Glosa',money(row.valor_glosa)]]);
    (row.fonte.motivos||[]).forEach(m=>el('p',`Motivo ${m.codigo || ''}: ${m.descricao || ''}${m.valor != null ? ' · '+money(m.valor):''}`,src));
    el('strong','Oracle',dst);
    if(row.oracle)facts(dst,[['Conta',row.oracle.cd_reg],['Lançamento',row.oracle.cd_lancamento],['Atendimento',row.oracle.cd_atendimento],['Guia',row.oracle.nr_guia],['Carteira',row.oracle.nr_carteira],['TUSS',row.oracle.cd_tuss],['Data realização',row.oracle.data_realizacao],['Quantidade',row.oracle.quantidade],['Valor',money(row.oracle.valor)]]);
    else el('p',`Sem vínculo confirmado. Candidatos: ${row.candidatos}.`,dst);
    (row.divergencias||[]).forEach(d=>el('p',d,dst,'petrobras-warning'));
    el('p',`Última sincronização: ${row.atualizado_em ? new Date(row.atualizado_em).toLocaleString('pt-BR'): 'Não realizada'}`,box);
    el('p',`Prazo: ${row.prazo || 'Não confirmado'}${row.origem_prazo ? ' · '+row.origem_prazo:''}`,box);
    if(row.recurso_protocolo)el('p',`Protocolo do recurso: ${row.recurso_protocolo}`,box);
    if(row.recurso_resultado){const d=el('details',null,box);el('summary','Resposta da operadora',d);el('pre',JSON.stringify(row.recurso_resultado,null,2),d);}
    const tools=el('div',null,box,'petrobras-actions');
    button(tools,'Conferir novamente no Oracle',async()=>{await api(`glosas/${row.id}/cruzar`,'POST',{});await refresh();notice('Conferência atualizada.');},readOnly);
    button(tools,'Consultar recurso',async()=>{await api(`glosas/${row.id}/consultar-recurso`,'POST',{});await refresh();notice('Situação consultada.');},readOnly||!row.recurso_protocolo);
    const history=el('details',null,box);el('summary','Histórico de alterações',history);
    history.addEventListener('toggle',()=>{if(history.open&&!history.dataset.loaded)action(async()=>{const events=await api(`glosas/${row.id}/historico`);events.forEach(e=>{el('p',`${new Date(e.data).toLocaleString('pt-BR')} · ${e.tipo}`,history);el('pre',JSON.stringify(e.dados,null,2),history);});history.dataset.loaded='true';notice('Histórico carregado.');});});
    const edit=el('details',null,box);el('summary','Justificativa e comprovantes',edit);
    const editable=!readOnly&&row.recurso_estado==='rascunho';
    const justification=input(edit,'Justificativa','textarea',row.justificativa,500);justification.disabled=!editable;
    const line=el('div',null,edit,'petrobras-actions');
    const due=input(line,'Prazo confirmado','date',row.prazo),origin=input(line,'Origem da regra do prazo','text',row.origem_prazo,500);due.disabled=origin.disabled=!editable;
    button(line,'Salvar rascunho',async()=>{await api(`glosas/${row.id}/rascunho`,'PUT',{versao:row.versao,justificativa:justification.value,prazo:due.value||null,origem_prazo:origin.value||null});await refresh();notice('Rascunho salvo.');},!editable);
    const docs=el('div',null,edit);
    button(docs,'Listar comprovantes',async()=>{docs.querySelectorAll('p').forEach(n=>n.remove());const files=await api(`glosas/${row.id}/anexos`);if(!files.length)el('p','Nenhum comprovante anexado.',docs);files.forEach(a=>{const line=el('p',null,docs),link=el('a',a.nome,line);link.href=base+`glosas/${row.id}/anexos/${a.id}`;el('span',` · ${labels[a.estado]||a.estado}${a.protocolo?' · Protocolo '+a.protocolo:''}`,line);});notice('Comprovantes carregados.');});
    if(editable){
      const upload=el('div',null,edit,'petrobras-actions');const file=input(upload,'Comprovante (PDF, PNG ou JPEG)','file');file.accept='.pdf,.png,.jpg,.jpeg';
      const kind=select(upload,'Tipo do documento TISS',[['','Selecione']]);
      // Populate labels from the official contract, supplied below; never infer codes.
      kind.replaceChildren();[['','Selecione'],...documentTypes].forEach(([v,t])=>{const o=el('option',t,kind);o.value=v;});
      const nature=select(upload,'Natureza da guia',[['','Selecione'],['1','Consulta'],['2','SP/SADT'],['3','Resumo de internação']]);
      button(upload,'Anexar ao item',async()=>{const f=file.files[0];if(!f||!kind.value||!nature.value)throw new Error('Selecione arquivo, tipo de documento e natureza da guia.');if(f.size>config.limite_anexo_bytes)throw new Error('Arquivo acima do limite configurado.');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Não foi possível ler o arquivo.'));reader.readAsDataURL(f);});await api(`glosas/${row.id}/anexos`,'POST',{nome:f.name,conteudo_base64:data,tipo_documento:kind.value,natureza_guia:nature.value});await refresh();notice('Comprovante anexado ao rascunho.');});
      const sendBox=el('details',null,edit);el('summary','Enviar recurso à Petrobras',sendBox);el('p','Salve a justificativa e revise os comprovantes antes de transmitir. O envio usa o rascunho salvo.',sendBox);
      const label=el('label',null,sendBox),confirm=el('input',null,label);confirm.type='checkbox';el('span',' Confirmo o envio deste recurso à Petrobras.',label);
      button(sendBox,'Transmitir recurso salvo',async()=>{if(!confirm.checked)throw new Error('Marque a confirmação de envio.');await api(`glosas/${row.id}/enviar`,'POST',{versao:row.versao,confirmar_envio:true});await refresh();notice('Consulte o protocolo apresentado no item.');},!config.transmissao_habilitada);
      if(!config.transmissao_habilitada)el('p','Transmissão ainda não habilitada neste ambiente.',sendBox);
    }
    if(['envio_incerto','transmitindo'].includes(row.recurso_estado)){el('p','Confira o protocolo no portal antes de qualquer nova tentativa.',box,'petrobras-warning');const link=el('a','Abrir portal Petrobras',box);link.href='https://portaltiss.saudepetrobras.com.br/saudeweb/seguranca/login';link.target='_blank';link.rel='noopener noreferrer';}
  }
  const documentTypes = ['Comprovante de elegibilidade','Laudo do profissional de saúde','Pedido do profissional de saúde','Relatório do profissional de saúde','Prescrição do profissional de saúde','Comprovante de comparecimento (assinatura)','Embalagem/rótulo de materiais ou medicamentos','Comprovante de entrega de medicamentos','Notas fiscais','Boletim anestésico','Selos hemoterápicos','Exames de imagem','Exames de análises clínicas','Termo de consentimento','Guia de cobrança de taxa','Autorização prévia','Relatório de Auditoria'].map((label,i)=>{const code=String(i+1).padStart(2,'0');return [code,`${code} · ${label}`];});
  async function refresh(){const params=new URLSearchParams();for(const [key,value] of new FormData(get('filters'))){if(String(value).trim())params.set(key,String(value).trim());}params.set('offset',offset);params.set('limit',20);const data=await api('glosas?'+params);total=data.total;get('cards').replaceChildren();data.items.forEach(card);if(!data.items.length)el('p','Nenhuma glosa importada para estes filtros.',get('cards'));get('count').textContent=`${total?offset+1:0}–${Math.min(offset+20,total)} de ${total}`;root.querySelector('[data-action=previous]').disabled=offset===0;root.querySelector('[data-action=next]').disabled=offset+20>=total;}
  async function protocols(){const rows=await api('protocolos');const s=get('protocol'),selected=s.value;s.replaceChildren();el('option','Selecione',s).value='';rows.forEach(r=>{const o=el('option',r.numero,s);o.value=r.id;});s.value=selected;}
  async function runs(){const rows=await api('execucoes');get('runs').replaceChildren();rows.slice(0,5).forEach(r=>el('p',`${new Date(r.inicio).toLocaleString('pt-BR')} · ${r.situacao}${r.erro?' · '+r.erro:''}`,get('runs')));}
  const actions={refresh:async()=>{await refresh();await runs();notice('Consulta atualizada.');},discover:async()=>{const competencia=get('competencia').value.replace('-','');if(!competencia)throw new Error('Selecione a competência.');const r=await api('competencias/sincronizar','POST',{competencia});await protocols();await refresh();await runs();notice(`${r.protocolos_localizados} protocolos localizados; ${r.quantidade || 0} registros importados. ${(r.avisos || []).join(' ')}`,r.situacao==='parcial');},sync:async()=>{const id=get('protocol').value;if(!id)throw new Error('Selecione um protocolo.');try{const r=await api(`protocolos/${id}/sincronizar`,'POST',{});await refresh();notice(`${r.quantidade || 0} registros importados. ${(r.avisos || []).join(' ')}`,r.situacao==='parcial');}finally{await runs();}},'add-protocol':async()=>{const numero=get('new-protocol').value.trim();await api('protocolos','POST',{numero});await protocols();notice('Protocolo adicionado.');},previous:async()=>{offset=Math.max(0,offset-20);await refresh();notice('Página anterior.');},next:async()=>{if(offset+20<total)offset+=20;await refresh();notice('Próxima página.');}};
  root.querySelectorAll('[data-action]').forEach(b=>{b.addEventListener('click',()=>action(actions[b.dataset.action]));if(readOnly&&['discover','sync','add-protocol'].includes(b.dataset.action))b.disabled=true;});
  get('filters').addEventListener('submit',e=>{e.preventDefault();offset=0;action(async()=>{await refresh();notice('Filtros aplicados.');});});
  action(async()=>{config=await api('status');if(!config.habilitado){notice('Integração Petrobras ainda não habilitada neste ambiente.');return;}await protocols();await refresh();await runs();notice(config.mapeamento_guia==='pendente'?'Mapeamento da guia Oracle pendente de validação. Os registros importados continuam disponíveis.':'Dados Petrobras carregados.');});
})();
