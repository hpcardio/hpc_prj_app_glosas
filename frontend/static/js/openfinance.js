(() => {
  'use strict';
  const button = document.getElementById('of-connect');
  const feedback = document.getElementById('of-feedback');
  if (!button || !feedback) return;
  let loading = false;
  let sdkPromise;
  const message = (text, error = false) => {
    feedback.textContent = text;
    feedback.hidden = false;
    feedback.classList.toggle('of-error', error);
  };
  const post = async (url, body = {}) => {
    const response = await fetch(url, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'Accept': 'application/json',
        'X-CSRFToken': document.querySelector('#of-csrf input[name=csrfmiddlewaretoken]').value},
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Não foi possível concluir. Confira seu acesso e tente novamente.');
    return data;
  };
  const loadSdk = () => {
    if (window.PluggyConnect) return Promise.resolve();
    if (sdkPromise) return sdkPromise;
    sdkPromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://cdn.pluggy.ai/pluggy-connect/v2.8.2/pluggy-connect.js';
      const timer = window.setTimeout(() => {script.remove(); sdkPromise = null; reject(new Error('A janela de conexão não carregou. Tente novamente.'));}, 20000);
      script.onload = () => {clearTimeout(timer); resolve();};
      script.onerror = () => {clearTimeout(timer); script.remove(); sdkPromise = null; reject(new Error('A janela de conexão não carregou. Tente novamente.'));};
      document.head.appendChild(script);
    });
    return sdkPromise;
  };
  button.addEventListener('click', async () => {
    if (loading) return;
    loading = true; button.disabled = true;
    try {
      await loadSdk();
      const data = await post(button.dataset.tokenUrl);
      if (!data.sandbox_only && !window.isSecureContext) throw new Error('Abra o endereço HTTPS para conectar sua conta.');
      const config = data.widget_config;
      if (!config || !Array.isArray(config.connectorIds) || !config.connectorIds.length) throw new Error('Conexão indisponível. Tente novamente.');
      let completed = false;
      const widget = new window.PluggyConnect({
        connectToken: data.accessToken,
        connectorIds: config.connectorIds,
        includeSandbox: config.includeSandbox === true,
        products: ['ACCOUNTS', 'TRANSACTIONS'], language: 'pt',
        onSuccess: async ({item}) => {
          if (completed) return;
          completed = true;
          try {
            await post(button.dataset.attachUrl, {item_id: item.id});
            message('Autorização concluída. As informações serão atualizadas em instantes. Consulte novamente para acompanhar.');
          } catch (_) {
            completed = false;
            message('A autorização foi recebida. Consulte novamente em instantes; se a conta não aparecer, tente a conexão outra vez.', true);
          } finally {loading = false; button.disabled = false;}
        },
        onError: () => {message('A conexão não foi concluída ou aguarda autorização. Confira o fluxo no banco e consulte novamente.', true); loading = false; button.disabled = false;},
        onClose: () => {loading = false; button.disabled = false;},
      });
      widget.init();
    } catch (error) {
      message(error.message || 'Conexão indisponível. Tente novamente.', true);
      loading = false; button.disabled = false;
    }
  });
})();
