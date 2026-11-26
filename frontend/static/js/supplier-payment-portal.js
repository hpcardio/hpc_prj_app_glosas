(() => {
  'use strict';
  const token=window.location.hash.slice(1);
  const csrf=document.querySelector('#su-payment-csrf input').value;
  const status=document.getElementById('su-payment-status');
  const money=value=>Number(value).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
  const body=new URLSearchParams({action:'consult',token,csrfmiddlewaretoken:csrf});
  fetch(window.location.pathname,{method:'POST',body,credentials:'same-origin'})
    .then(async response=>{const data=await response.json();if(!response.ok)throw new Error(data.error||'Aviso indisponível.');return data;})
    .then(data=>{
      status.hidden=true;
      document.getElementById('su-payment-content').hidden=false;
      document.getElementById('su-payment-supplier').textContent=data.name+' · '+data.document;
      document.getElementById('su-payment-company').textContent='Empresa pagadora: '+(data.company_name||data.company);
      const table=document.getElementById('su-payment-items');
      data.items.forEach(item=>{
        const row=document.createElement('tr');
        [item.reference,item.date.split('-').reverse().join('/'),money(item.amount)].forEach(value=>{const cell=document.createElement('td');cell.textContent=value;row.append(cell);});table.append(row);
      });
      document.getElementById('su-payment-total').textContent='Total comunicado: '+money(data.total);
      const downloads=document.getElementById('su-payment-downloads');
      [{id:'aviso',name:'Baixar aviso de pagamento em PDF'},...data.files].forEach(file=>{
        const form=document.createElement('form');form.method='post';form.action=window.location.pathname;
        Object.entries({token,action:'download',file:file.id,csrfmiddlewaretoken:csrf}).forEach(([name,value])=>{
          const input=document.createElement('input');input.type='hidden';input.name=name;input.value=value;form.append(input);
        });
        const button=document.createElement('button');button.className='su-btn su-btn--light';button.textContent=file.name;form.append(button);downloads.append(form);
      });
    })
    .catch(error=>{status.textContent=error.message||'Não foi possível consultar o aviso. Entre em contato com o financeiro.';status.classList.add('su-alert--error');});
})();
