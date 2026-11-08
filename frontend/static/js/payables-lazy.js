(() => {
  'use strict';
  const pending = [];
  let active = 0;

  function enqueue(task) {
    return new Promise((resolve, reject) => {
      pending.push({task, resolve, reject});
      drain();
    });
  }

  function drain() {
    while (active < 2 && pending.length) {
      const next = pending.shift();
      active++;
      Promise.resolve().then(next.task).then(next.resolve, next.reject).finally(() => {
        active--;
        drain();
      });
    }
  }

  function load(slot) {
    if (!slot || slot.dataset.loaded === 'true') return Promise.resolve();
    if (slot._loading) return slot._loading;
    const status = slot.querySelector('[role="status"]');
    const retry = slot.querySelector('[data-lazy-retry]');
    if (retry) retry.hidden = true;
    if (status) status.textContent = 'Carregando informações...';
    slot.setAttribute('aria-busy', 'true');
    slot._loading = enqueue(async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 55000);
      try {
        const summary = slot.hasAttribute('data-payables-summary');
        const matrix = slot.hasAttribute('data-payables-matrix');
        const kind = summary ? 'summary' : (matrix ? 'matrix' : 'detail');
        const url = new URL(window.location.href);
        url.searchParams.delete('export');
        url.searchParams.delete('fragmento');
        url.searchParams.delete('detalhes_fornecedor');
        if (summary || matrix) url.searchParams.set('fragmento', summary ? 'resumo' : 'matriz');
        else url.searchParams.set('detalhes_fornecedor', slot.dataset.payablesDetail);
        const response = await fetch(url, {
          credentials: 'same-origin', redirect: 'error', cache: 'no-store', signal: controller.signal,
        });
        if (summary && response.status === 409) {
          const target = new URL(response.headers.get('X-ReceitaCerta-Redirect'), window.location.href);
          if (target.origin !== url.origin) throw new Error('Invalid redirect');
          window.location.assign(target.href);
          return;
        }
        if (!response.ok || response.headers.get('X-ReceitaCerta-Fragment') !== 'payables-' + kind) {
          throw new Error('Unavailable fragment');
        }
        const html = await response.text();
        if (summary) {
          const template = document.createElement('template');
          template.innerHTML = html;
          const content = template.content.firstElementChild;
          if (!content?.matches('.payables-page')) throw new Error('Invalid page content');
          slot.dataset.loaded = 'true';
          slot.replaceWith(content);
          content.dispatchEvent(new CustomEvent('htmx:afterSwap', {bubbles: true}));
          startMatrices();
        } else {
          slot.innerHTML = html;
          slot.dataset.loaded = 'true';
          slot.classList.remove('panel');
          slot.dispatchEvent(new CustomEvent('htmx:afterSwap', {bubbles: true}));
        }
      } catch (_) {
        if (status) status.textContent = 'Não foi possível carregar as informações. Tente novamente.';
        if (retry) retry.hidden = false;
      } finally {
        clearTimeout(timer);
        slot.setAttribute('aria-busy', 'false');
      }
    }).finally(() => { slot._loading = null; });
    return slot._loading;
  }

  function startMatrices() {
    document.querySelectorAll('[data-payables-matrix]').forEach(load);
  }

  window.loadPayablesDetail = root => load(root.querySelector('[data-payables-detail]'));
  document.addEventListener('click', event => {
    const retry = event.target.closest('[data-lazy-retry]');
    if (retry) load(retry.closest('[data-lazy-fragment]'));
  });
  const start = () => {
    document.querySelectorAll('[data-payables-summary]').forEach(load);
    startMatrices();
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
