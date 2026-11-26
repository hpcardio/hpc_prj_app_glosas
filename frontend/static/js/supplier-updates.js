/* Supplier portal uses the same server rules; declarations never write MV. */
(() => {
  'use strict';
  document.querySelector('[data-select-all]')?.addEventListener('change', event => {
    document.querySelectorAll('input[name="selected"]').forEach(input => { input.checked = event.target.checked; });
  });
  document.querySelectorAll('[data-confirm]').forEach(button => button.addEventListener('click', event => {
    if (!window.confirm(button.dataset.confirm)) event.preventDefault();
  }));
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const field = document.getElementById(button.dataset.copy);
    try { await navigator.clipboard.writeText(field.value); button.textContent = 'Copiado'; }
    catch { field.focus(); field.select(); button.textContent = 'Selecione e copie o texto'; }
  }));
  document.querySelector('[data-whatsapp]')?.addEventListener('submit', event => {
    const form = event.target;
    let phone = form.elements.destination.value.replace(/\D/g, '');
    if (phone.length === 10 || phone.length === 11) phone = '55' + phone;
    if (phone.length < 12 || phone.length > 15) { event.preventDefault(); window.alert('Confira o WhatsApp com DDD e código do país.'); return; }
    const url = new URL(form.dataset.whatsapp);
    url.pathname = '/' + phone;
    window.open(url.toString(), '_blank', 'noopener,noreferrer');
  });
  const decision = document.querySelector('select[name="decision"]');
  if (decision) {
    const reviewBalance = document.querySelector('input[name="balance"]');
    const requireBalance = () => { reviewBalance.required = decision.value === 'aprovar'; };
    decision.addEventListener('change', requireBalance);
    requireBalance();
  }
  const form = document.getElementById('su-public-form');
  if (!form) return;
  const tokenInput = document.getElementById('su-token');
  tokenInput.value = tokenInput.value || window.location.hash.slice(1);
  document.querySelectorAll('[data-scroll]').forEach(button => button.addEventListener('click', () => {
    document.getElementById(button.dataset.scroll).scrollIntoView({behavior:'smooth',block:'start'});
  }));
  let invitationValid = false;
  const info = document.getElementById('su-invite-info');
  document.getElementById('su-submit').disabled = true;
  const consultation = new URLSearchParams({action:'consult', token:tokenInput.value,
    csrfmiddlewaretoken: form.elements.csrfmiddlewaretoken.value});
  fetch(window.location.pathname, {method:'POST', body:consultation, credentials:'same-origin'})
    .then(async response => { const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Convite indisponível.'); return data; })
    .then(data => {
      info.textContent = `${data.name} · ${data.document || 'Documento a informar'} · empresa destinatária ${data.company}`;
      if (!field('document').value) field('document').value = data.document || '';
      invitationValid = true;
      document.getElementById('su-submit').disabled = false;
    })
    .catch(error => { info.textContent = error.message || 'Não foi possível conferir o convite. Solicite apoio ao financeiro.'; info.classList.add('su-alert--error'); });
  const saved = JSON.parse(document.getElementById('su-saved-payload').textContent || '{}');
  const field = name => form.querySelector(`[data-field="${name}"]`);
  const error = document.getElementById('su-form-error');
  const titles = document.getElementById('su-title-rows');
  const agreements = document.getElementById('su-agreement-rows');
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  field('as_of').max = localDate;
  field('as_of').value = localDate;
  for (const [name, value] of Object.entries(saved)) {
    const input = field(name);
    if (!input) continue;
    if (input.type === 'checkbox') input.checked = value === true;
    else input.value = value ?? '';
  }
  function fill(element, values) {
    element.querySelectorAll('[data-key]').forEach(input => {
      if (values[input.dataset.key] != null) input.value = Array.isArray(values[input.dataset.key]) ? values[input.dataset.key].join(', ') : values[input.dataset.key];
    });
  }
  function add(template, container, values={}) {
    const element = document.getElementById(template).content.firstElementChild.cloneNode(true);
    fill(element, values);
    element.querySelector('.su-remove').addEventListener('click', () => { element.remove(); update(); });
    container.append(element);
    return element;
  }
  function addTitle(values={}) { return add('su-title-template', titles, values); }
  function addAgreement(values={}) {
    const element = add('su-agreement-template', agreements, values);
    const container = element.querySelector('.su-installments');
    element.querySelector('.su-add-installment').addEventListener('click', () => add('su-installment-template', container));
    (values.installments || []).forEach(p => add('su-installment-template', container, p));
    return element;
  }
  document.getElementById('su-add-title').addEventListener('click', () => addTitle().querySelector('input').focus());
  document.getElementById('su-add-agreement').addEventListener('click', () => addAgreement().querySelector('input').focus());
  (saved.titles || []).forEach(addTitle);
  (saved.agreements || []).forEach(addAgreement);
  if (!saved.titles?.length) addTitle();
  function values(element, skipNested=false) {
    const result = {};
    element.querySelectorAll('[data-key]').forEach(input => {
      if (skipNested && input.closest('.su-installment')) return;
      result[input.dataset.key] = input.value.trim();
    });
    return result;
  }
  function payload() {
    const data = {};
    form.querySelectorAll('[data-field]').forEach(input => { data[input.dataset.field] = input.type === 'checkbox' ? input.checked : input.value.trim(); });
    data.statement_balance = data.statement_balance === '' ? null : data.statement_balance;
    data.titles = [];
    data.agreements = [];
    if (data.no_debt) { data.statement_only = false; data.statement_balance = null; }
    else if (!data.statement_only) {
      data.statement_balance = null;
      data.titles = [...titles.querySelectorAll('.su-title')].map(element => values(element)).filter(t => Object.values(t).some(Boolean)).map(t => ({...t, issued: t.issued || null}));
      data.agreements = [...agreements.querySelectorAll('.su-agreement-block')].map(element => {
        const agreement = values(element, true);
        agreement.replaces = agreement.replaces.split(/[,;\n]/).map(v => v.trim()).filter(Boolean);
        agreement.installments = [...element.querySelectorAll('.su-installment')].map(p => values(p)).filter(p => Object.values(p).some(Boolean));
        return agreement;
      }).filter(a => a.reference || a.balance || a.notes || a.replaces.length || a.installments.length);
    }
    return data;
  }
  function cents(value) { const n = Number(value || 0); return Number.isFinite(n) ? Math.round(n * 100) : 0; }
  function update() {
    const data = payload();
    document.getElementById('su-debt-fields').hidden = data.no_debt;
    document.getElementById('su-structured').hidden = data.statement_only;
    document.getElementById('su-statement').hidden = !data.statement_only;
    document.getElementById('acordos').hidden = data.no_debt || data.statement_only;
    const replaced = new Set(data.agreements.flatMap(a => a.replaces));
    const total = data.no_debt ? 0 : data.statement_only ? cents(data.statement_balance) :
      data.titles.filter(t => !replaced.has(t.reference)).reduce((s,t) => s+cents(t.balance),0) + data.agreements.reduce((s,a) => s+cents(a.balance),0);
    document.getElementById('su-declared-total').textContent = (total/100).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
    document.getElementById('su-summary-date').textContent = data.as_of || '—';
  }
  form.addEventListener('input', update);
  form.addEventListener('change', update);
  form.addEventListener('submit', event => {
    if (!invitationValid) { event.preventDefault(); return; }
    error.hidden = true;
    const data = payload();
    const files = [...document.getElementById('su-files').files];
    let message = '';
    if (!data.no_debt && !data.statement_only && !data.titles.length && !data.agreements.length) message = 'Informe os títulos/acordos ou marque a opção de demonstrativo anexo.';
    if (data.statement_only && (data.statement_balance == null || !files.length)) message = 'Informe o saldo total e anexe o demonstrativo.';
    for (const t of data.titles) if (!t.reference || !t.due || t.amount === '' || t.balance === '') message = 'Preencha referência, vencimento, valor e saldo de cada título.';
    for (const a of data.agreements) {
      if (!a.reference || a.balance === '') message = 'Informe referência e saldo de cada acordo.';
      for (const p of a.installments) if (!p.reference || !p.due || p.amount === '') message = 'Complete os dados das parcelas do acordo.';
      if (a.installments.length && a.installments.reduce((s,p) => s+cents(p.amount),0) !== cents(a.balance)) message = 'O saldo do acordo deve corresponder à soma das parcelas restantes.';
    }
    if (files.length > 10 || files.some(f => f.size > 10*1024*1024) || files.reduce((s,f) => s+f.size,0) > 25*1024*1024) message = 'Envie até 10 arquivos, de até 10 MB cada, somando no máximo 25 MB.';
    if (message) { event.preventDefault(); error.textContent = message; error.hidden = false; error.scrollIntoView({block:'center'}); return; }
    if (!window.confirm('Conferiu o cadastro, a data-base, os saldos e os documentos? Após o envio, as informações serão analisadas pelo financeiro.')) { event.preventDefault(); return; }
    document.getElementById('su-payload').value = JSON.stringify(data);
    document.getElementById('su-submit').disabled = true;
    document.getElementById('su-submit').textContent = 'Enviando informações…';
  });
  update();
})();
