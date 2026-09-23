const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const base = fs.readFileSync(path.join(root, 'templates/base.html'), 'utf8');
const follow = fs.readFileSync(path.join(root, 'templates/follow_up_glosas.html'), 'utf8');
const start = base.indexOf('window.applyOperationState = function');
const code = base.slice(start, base.indexOf('  </script>', start));
const methodStart = follow.indexOf('openTreatment(tipo) {');
const methodEnd = follow.indexOf('formatValorTratado() {', methodStart);
const openTreatment = vm.runInNewContext(`({${follow.slice(methodStart, methodEnd)}}).openTreatment`);
function scenario(followUp) {
  const state = { registroBaseId: '1', registroAcatoId: '20', processoAcato: 'ACATO', openTreatment: followUp ? openTreatment : undefined };
  const flags = () => ({ classList: { add() {}, remove() {}, toggle() {} } });
  const fields = (tipo) => ['sn_glosado', 'processo_recurso', 'dt_recurso', 'qtd_glosada', 'valor_glosado', 'descricao_glosa'].map(name => ({ name, type: name === 'dt_recurso' ? 'date' : 'text', value: name === 'sn_glosado' ? tipo : 'anterior', defaultValue: 'anterior' }));
  const forms = ['true', 'not'].map(tipo => ({ fields: fields(tipo), querySelector(selector) { return selector.includes('sn_glosado') ? this.fields[0] : null; }, querySelectorAll() { return this.fields; } }));
  const row = { querySelector(selector) { return selector.includes('btn-') ? flags() : null; }, querySelectorAll() { return followUp ? [forms[0]] : forms; } };
  forms.forEach(form => { form.closest = () => row; });
  const window = { Alpine: { $data: () => state }, updateRegistroFormActions(form, has, id) { form.savedId = has ? id : ''; }, clearGlosaFormFields(form) { form.fields.forEach(field => field.value = ''); } };
  vm.runInNewContext(code, { window });
  return { window, state, forms };
}
for (const tipo of ['true', 'not']) {
  test(`Follow-Up reabre ${tipo} com os dados confirmados`, () => {
    const { window, state, forms } = scenario(true);
    window.applyOperationState(forms[0], { payload: { id: 99, sn_glosado: tipo, processo_recurso: 'NOVO', numero_lote: '78', qtd_recursado: '2', valor_recursado: '580.95', dt_recurso: '2026-09-14', descricao_glosa: 'Descrição atualizada' } });
    state.openTreatment(tipo === 'not' ? 'acatar' : 'glosar');
    assert.equal(state.processoTratado, 'NOVO');
    assert.equal(state.registroTratadoId, 99);
    assert.equal(state.descricaoTratada, 'Descrição atualizada');
    assert.equal(state.qtdTratada, '2');
    assert.equal(state.editingExisting, true);
    if (tipo === 'true') assert.equal(state.processoAcato, 'ACATO');
  });
  test(`Triagem mantém os campos salvos de ${tipo}`, () => {
    const { window, forms } = scenario(false);
    const form = forms[tipo === 'not' ? 1 : 0];
    window.applyOperationState(form, { payload: { id: 99, sn_glosado: tipo, processo_recurso: 'NOVO', dt_recurso: '2026-09-14', qtd_recursado: '2', valor_recursado: '580.95', descricao_glosa: 'Nova descrição' } });
    assert.equal(form.fields[1].value, 'NOVO');
    assert.equal(form.fields[1].defaultValue, 'NOVO');
    assert.equal(form.fields[2].value, '2026-09-14');
    assert.equal(form.savedId, 99);
    assert.equal(forms[tipo === 'not' ? 0 : 1].savedId, undefined);
  });
}
test('desfazer acato preserva o recurso do Follow-Up', () => {
  const { window, state, forms } = scenario(true);
  state.registroRecusaId = '10';
  state.processoRecusa = 'RECURSO';
  window.applyOperationState(forms[0], { operationType: 'not' });
  state.openTreatment('glosar');
  assert.equal(state.registroTratadoId, '10');
  assert.equal(state.processoTratado, 'RECURSO');
  state.openTreatment('acatar');
  assert.equal(state.editingExisting, false);
});
