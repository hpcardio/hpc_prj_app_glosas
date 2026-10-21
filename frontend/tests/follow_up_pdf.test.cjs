const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const base = fs.readFileSync(path.join(__dirname, '../templates/base.html'), 'utf8');
const start = base.indexOf('window.updateFollowUpPdfAfterSave = function');
const code = base.slice(start, base.indexOf('window.applyOperationState = function', start));
function scenario(url = '/follow-up-glosas/recurso-pdf/?processo_original=2600035789') {
  const links = [];
  const slot = { dataset: { recursoPdfUrl: url, recursoProcesso: '2600035789' },
    querySelector: () => links[0], appendChild: link => links.push(link) };
  const form = { closest: () => ({ querySelector: () => slot }) };
  const window = {};
  const document = { createElement: () => ({ setAttribute(name, value) { this[name] = value; } }) };
  vm.runInNewContext(code, { window, document });
  return { links, form, update: data => window.updateFollowUpPdfAfterSave(form, data) };
}
test('recurso individual cria PDF do processo sem refresh e sem duplicar', () => {
  const s = scenario();
  const data = { ok: true, payload: { id: 10, sn_glosado: 'true' } };
  s.update(data); s.update(data);
  assert.equal(s.links.length, 1);
  assert.equal(s.links[0].href, '/follow-up-glosas/recurso-pdf/?processo_original=2600035789');
  assert.equal(s.links[0].textContent, 'PDF');
  assert.equal(s.links[0].rel, 'noopener');
});
test('recurso multiplo cria o mesmo botao', () => {
  const s = scenario();
  s.update({ ok: true, payload: { sn_glosado: 'true', itens: [{ id: 10 }, { id: 11 }] } });
  assert.equal(s.links.length, 1);
});
test('erro, acato, exclusao e resposta sem registro nao criam PDF', () => {
  for (const data of [
    { ok: false, payload: { id: 10, sn_glosado: 'true' } },
    { ok: true, payload: { id: 10, sn_glosado: 'not' } },
    { ok: true, operationType: 'true', payload: {} },
    { ok: true, payload: { sn_glosado: 'true', itens: [] } },
  ]) {
    const s = scenario(); s.update(data); assert.equal(s.links.length, 0);
  }
});
test('processo ausente e outras telas nao recebem link', () => {
  const data = { ok: true, operationType: 'true', payload: { id: 10 } };
  const s = scenario(''); s.update(data); assert.equal(s.links.length, 0);
  s.form.closest = () => null; s.update(data); assert.equal(s.links.length, 0);
});
test('a confirmacao de sucesso chama a atualizacao antes de fechar o modal', () => {
  assert.match(base, /if \(form && data.ok\) \{\s*window.updateFollowUpPdfAfterSave\(form, data\);/);
});
