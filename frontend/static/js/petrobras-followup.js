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
  const labels = {conciliado:'Conciliado',nao_localizado:'Não localizado no Oracle',ambiguo:'Correspondência ambígua',dados_insuficientes:'Dados insuficientes',rascunho:'Rascunho',protocolado:'Protocolado',em_analise:'Em análise',respondido:'Respondido',envio_incerto:'Envio não confirmado',transmitindo:'Transmissão iniciada'};
  let config = {}, busy = false, configLoaded = null;
  function el(tag, text, parent, cls) { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; if (parent) parent.append(n); return n; }
  function notice(text, error=false) {
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
  function render(row) {
    const box = get('dialog-body'); box.replaceChildren(); get('dialog-message').textContent = '';
    if(!row.presente_na_fonte)el('p','Não consta na última versão do demonstrativo; histórico preservado.',box,'petrobras-warning');
    if(row.fonte.cobertura==='parcial_nao_confirmada')el('p','Retorno parcial da operadora. Os totais gerais podem incluir outras guias.',box,'petrobras-warning');
    (row.fonte.avisos||[]).forEach(message=>el('p',message,box,'petrobras-warning'));
    if(row.erro_oracle)el('p',row.erro_oracle,box,'petrobras-warning');
    const compare=el('div',null,box,'petrobras-comparison'),src=el('div',null,compare),dst=el('div',null,compare);
    el('strong','Petrobras',src);
    facts(src,[['Demonstrativo',row.demonstrativo],['Protocolo',row.fonte.protocolo],['Guia prestador',row.guia_prestador],['Guia operadora',row.guia_operadora],['Senha',row.fonte.senha],['Carteira',row.fonte.carteira],['Código do procedimento',row.tuss],['Procedimento',row.fonte.descricao],['Data realização',row.fonte.data_realizacao],['Quantidade',row.fonte.quantidade],['Valor informado',money(row.fonte.valor_informado)],['Glosa',money(row.valor_glosa)]]);
    (row.fonte.motivos||[]).forEach(m=>el('p',`Motivo ${m.codigo || ''}${m.valor != null ? ' · '+money(m.valor):''}`,src));
    el('strong',`Oracle · ${labels[row.cruzamento] || row.cruzamento}`,dst);
    if(row.oracle)facts(dst,[['Paciente',row.oracle.nm_paciente],['Atendimento',row.oracle.cd_atendimento],['Remessa',row.oracle.cd_remessa],['Conta',row.oracle.cd_reg],['Lançamento',row.oracle.cd_lancamento],['Guia',row.oracle.nr_guia],['Carteira',row.oracle.nr_carteira],['CD_PRO_FAT',row.oracle.cd_pro_fat],['Data lançamento',row.oracle.data_realizacao],['Quantidade',row.oracle.quantidade],['Valor',money(row.oracle.valor)]]);
    else el('p',`Sem vínculo confirmado pela chave carteira + guia do prestador + procedimento. Candidatos: ${row.candidatos}.`,dst);
    (row.divergencias||[]).forEach(d=>el('p',d,dst,'petrobras-warning'));
    el('p',`Prazo: ${row.prazo || 'Não confirmado'}${row.origem_prazo ? ' · '+row.origem_prazo:''}`,box);
    if(row.recurso_protocolo)el('p',`Protocolo do recurso: ${row.recurso_protocolo}`,box);
    if(row.recurso_resultado){const d=el('details',null,box);el('summary','Resposta da operadora',d);el('pre',JSON.stringify(row.recurso_resultado,null,2),d);}
    const tools=el('div',null,box,'petrobras-actions');
    button(tools,'Conferir novamente no Oracle',async()=>{await api(`glosas/${row.id}/cruzar`,'POST',{});reload('Conferência atualizada.');},readOnly);
    button(tools,'Consultar recurso',async()=>{await api(`glosas/${row.id}/consultar-recurso`,'POST',{});reload('Situação consultada.');},readOnly||!row.recurso_protocolo);
    const editable=!readOnly&&row.recurso_estado==='rascunho';
    const edit=el('section',null,box,'petrobras-appeal');el('h3','Justificativa e comprovantes',edit);
    const justification=input(edit,'Justificativa','textarea',row.justificativa,500);justification.disabled=!editable;
    const line=el('div',null,edit,'petrobras-actions');
    const due=input(line,'Prazo confirmado','date',row.prazo),origin=input(line,'Origem da regra do prazo','text',row.origem_prazo,500);due.disabled=origin.disabled=!editable;
    button(line,'Salvar rascunho',async()=>{await api(`glosas/${row.id}/rascunho`,'PUT',{versao:row.versao,justificativa:justification.value,prazo:due.value||null,origem_prazo:origin.value||null});reload('Rascunho salvo.');},!editable);
    const docs=el('div',null,edit);
    button(docs,'Listar comprovantes',async()=>{docs.querySelectorAll('p').forEach(n=>n.remove());const files=await api(`glosas/${row.id}/anexos`);if(!files.length)el('p','Nenhum comprovante anexado.',docs);files.forEach(a=>{const p=el('p',null,docs),link=el('a',a.nome,p);link.href=base+`glosas/${row.id}/anexos/${a.id}`;el('span',` · ${labels[a.estado]||a.estado}${a.protocolo?' · Protocolo '+a.protocolo:''}`,p);});notice('Comprovantes carregados.');});
    if(editable){
      const upload=el('div',null,edit,'petrobras-actions');const file=input(upload,'Comprovante (PDF, PNG ou JPEG)','file');file.accept='.pdf,.png,.jpg,.jpeg';
      const kind=select(upload,'Tipo do documento TISS',[['','Selecione'],...documentTypes]);
      const nature=select(upload,'Natureza da guia',[['','Selecione'],['1','Consulta'],['2','SP/SADT'],['3','Resumo de internação']]);
      button(upload,'Anexar ao item',async()=>{const f=file.files[0];if(!f||!kind.value||!nature.value)throw new Error('Selecione arquivo, tipo de documento e natureza da guia.');if(f.size>config.limite_anexo_bytes)throw new Error('Arquivo acima do limite configurado.');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('Não foi possível ler o arquivo.'));reader.readAsDataURL(f);});await api(`glosas/${row.id}/anexos`,'POST',{nome:f.name,conteudo_base64:data,tipo_documento:kind.value,natureza_guia:nature.value});reload('Comprovante anexado ao rascunho.');});
      const sendBox=el('details',null,edit);el('summary','Enviar recurso à Petrobras',sendBox);el('p','Salve a justificativa e revise os comprovantes antes de transmitir. O envio usa o rascunho salvo.',sendBox);
      const label=el('label',null,sendBox),confirm=el('input',null,label);confirm.type='checkbox';el('span',' Confirmo o envio deste recurso à Petrobras.',label);
      button(sendBox,'Transmitir recurso salvo',async()=>{if(!confirm.checked)throw new Error('Marque a confirmação de envio.');await api(`glosas/${row.id}/enviar`,'POST',{versao:row.versao,confirmar_envio:true});reload('Consulte o protocolo apresentado no item.');},!config.transmissao_habilitada);
      if(!config.transmissao_habilitada)el('p','Transmissão ainda não habilitada neste ambiente.',sendBox);
    }
    if(['envio_incerto','transmitindo'].includes(row.recurso_estado)){el('p','Confira o protocolo no portal antes de qualquer nova tentativa.',box,'petrobras-warning');const link=el('a','Abrir portal Petrobras',box);link.href='https://portaltiss.saudepetrobras.com.br/saudeweb/seguranca/login';link.target='_blank';link.rel='noopener noreferrer';}
    const history=el('details',null,box);el('summary','Histórico de alterações',history);
    history.addEventListener('toggle',()=>{if(history.open&&!history.dataset.loaded)action(async()=>{const events=await api(`glosas/${row.id}/historico`);events.forEach(e=>{el('p',`${new Date(e.data).toLocaleString('pt-BR')} · ${e.tipo}`,history);el('pre',JSON.stringify(e.dados,null,2),history);});history.dataset.loaded='true';notice('Histórico carregado.');});});
  }
  async function loadConfig() { configLoaded = configLoaded || api('status').then(data => { config = data; }); return configLoaded; }
  document.addEventListener('click', event => {
    const trigger = event.target.closest('[data-petrobras-item]');
    if (!trigger) return;
    let row;
    try { row = JSON.parse(trigger.dataset.petrobrasItem); } catch (_) { return; }
    action(async () => { await loadConfig(); render(row); if (!dialog.open) dialog.showModal(); notice(''); });
  });
  dialog.querySelector('[data-dialog-close]').addEventListener('click', () => dialog.close());
  async function protocols(){const rows=await api('protocolos');const s=get('protocol'),selected=s.value;s.replaceChildren();el('option','Selecione',s).value='';rows.forEach(r=>{const o=el('option',r.numero,s);o.value=r.id;});s.value=selected;}
  async function runs(){const rows=await api('execucoes');get('runs').replaceChildren();rows.slice(0,5).forEach(r=>el('p',`${new Date(r.inicio).toLocaleString('pt-BR')} · ${r.situacao}${r.erro?' · '+r.erro:''}`,get('runs')));}
  const actions={
    discover:async()=>{const competencia=get('competencia').value.replace('-','');if(!competencia)throw new Error('Selecione a competência.');const r=await api('competencias/sincronizar','POST',{competencia});reload(`${r.protocolos_localizados} protocolos localizados; ${r.quantidade || 0} registros importados. ${(r.avisos || []).join(' ')}`);},
    sync:async()=>{const id=get('protocol').value;if(!id)throw new Error('Selecione um protocolo.');const r=await api(`protocolos/${id}/sincronizar`,'POST',{});reload(`${r.quantidade || 0} registros importados. ${(r.avisos || []).join(' ')}`);},
    'add-protocol':async()=>{const numero=get('new-protocol').value.trim();await api('protocolos','POST',{numero});await protocols();notice('Protocolo adicionado.');},
    'match-all':async()=>{const r=await api('glosas/cruzar','POST',{});reload(`${r.conferidos} registros conferidos no Oracle; ${r.alterados} atualizados.`);},
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
