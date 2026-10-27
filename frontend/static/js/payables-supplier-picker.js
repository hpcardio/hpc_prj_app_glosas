(() => {
  "use strict";
  const normalize = value => String(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase();
  document.querySelectorAll("[data-supplier-picker]").forEach(root => {
    const options = JSON.parse(document.getElementById("supplier-picker-options").textContent);
    const initial = JSON.parse(document.getElementById("supplier-picker-selected").textContent);
    const selected = new Map(initial.map(item => [String(item.codigo), item]));
    const search = root.querySelector(".supplier-picker-search");
    const chips = root.querySelector("[data-supplier-chips]");
    const ids = root.querySelector("[data-supplier-ids]");
    const query = root.querySelector("[data-supplier-query]");
    const results = root.querySelector("[data-supplier-results]");
    const status = root.querySelector("[data-supplier-status]");
    function renderChips() {
      chips.replaceChildren();
      selected.forEach((item, id) => {
        const chip = document.createElement("span");
        chip.className = "supplier-picker-chip";
        const name = document.createElement("span");
        name.textContent = item.nome;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "\u00d7";
        remove.setAttribute("aria-label", "Remover " + item.nome);
        remove.addEventListener("click", () => {
          selected.delete(id);
          renderChips();
          renderResults();
          search.focus();
        });
        chip.append(name, remove);
        chips.append(chip);
      });
      ids.value = [...selected.keys()].join(",");
      query.value = selected.size ? "" : search.value.trim();
    }
    function renderResults() {
      const term = normalize(search.value.trim());
      results.replaceChildren();
      results.hidden = !term;
      status.classList.toggle("supplier-picker-assistive", !term);
      if (!term) {
        status.textContent = selected.size ? `${selected.size} fornecedor(es) selecionado(s).` : "Todos os fornecedores.";
        return;
      }
      const matches = options.filter(item => normalize(item.nome).includes(term) || String(item.codigo).includes(term));
      status.textContent = matches.length
        ? `${matches.length} encontrado(s). Mostrando ${Math.min(20, matches.length)}. ${selected.size} selecionado(s).`
        : "Nenhum fornecedor encontrado. Ajuste a pesquisa.";
      matches.slice(0, 20).forEach(item => {
        const id = String(item.codigo);
        const label = document.createElement("label");
        label.className = "supplier-picker-option";
        const check = document.createElement("input");
        check.type = "checkbox";
        check.checked = selected.has(id);
        const text = document.createElement("span");
        text.textContent = item.nome + (item.sem_cadastro ? " \u00b7 Sem cadastro no MV" : " \u00b7 " + id);
        check.addEventListener("change", () => {
          if (check.checked) selected.set(id, item); else selected.delete(id);
          renderChips();
          status.textContent = `${selected.size} fornecedor(es) selecionado(s).`;
        });
        label.append(check, text);
        results.append(label);
      });
    }
    search.addEventListener("input", () => { renderResults(); query.value = selected.size ? "" : search.value.trim(); });
    search.addEventListener("keydown", event => {
      if (event.key === "Escape") { search.value = ""; renderResults(); }
    });
    root.closest("form").addEventListener("submit", () => {
      ids.value = [...selected.keys()].join(",");
      query.value = selected.size ? "" : search.value.trim();
    });
    renderChips();
    renderResults();
  });
})();
