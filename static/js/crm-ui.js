'use strict';
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.nav-item.active').forEach(a => a.setAttribute('aria-current','page'));
  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      const input=document.getElementById('globalSearch'); if(input){e.preventDefault();input.focus();}
    }
  });
  document.querySelectorAll('[data-record-list]').forEach(panel => {
    const input=panel.querySelector('[data-table-search]'), rows=[...panel.querySelectorAll('tbody tr')].filter(r=>!r.querySelector('[colspan]'));
    const count=panel.querySelector('[data-table-count]');
    const select=panel.querySelector('[data-table-filter]');
    if(select){const col=Number(select.dataset.tableFilter);[...new Set(rows.map(r=>r.cells[col]?.textContent.trim()).filter(Boolean))].sort().forEach(value=>{const o=document.createElement('option');o.value=value;o.textContent=value;select.append(o);});select.addEventListener('change',filter);}
    const empty=document.createElement('p');empty.className='table-filter-empty';empty.textContent='No matching records. Try another search.';empty.hidden=true;panel.append(empty);
    function filter(){let shown=0;const q=input.value.trim().toLocaleLowerCase();rows.forEach(row=>{row.hidden=!row.textContent.toLocaleLowerCase().includes(q)||(select&&select.value&&row.cells[Number(select.dataset.tableFilter)].textContent.trim()!==select.value);if(!row.hidden)shown++;});count.textContent=`${shown} of ${rows.length} records`;empty.hidden=shown>0||rows.length===0;}
    input.addEventListener('input',filter);filter();
  });
  document.querySelectorAll('.data-panel tbody td:first-child>a').forEach(a=>{
    const name=a.textContent.trim();if(!name)return;
    const badge=document.createElement('span');badge.className='record-monogram';badge.setAttribute('aria-hidden','true');badge.textContent=name.split(/\s+/).slice(0,2).map(x=>x[0]).join('').toUpperCase();a.prepend(badge);
  });
  const deal=document.querySelector('select[name="deal_id"]'),lead=document.querySelector('select[name="lead_id"]');
  if(deal&&lead){deal.addEventListener('change',()=>{if(deal.value)lead.value='';});lead.addEventListener('change',()=>{if(lead.value)deal.value='';});}
  // Keep keyboard focus within the mobile navigation while it is open.
  document.addEventListener('keydown',e=>{const side=document.querySelector('.sidebar.open');if(!side||e.key!=='Tab')return;const controls=[...side.querySelectorAll('a,button')].filter(x=>x.getClientRects().length);const first=controls[0],last=controls[controls.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}});
});

// Company/contact selectors use the same company relationship validated on the server.
document.addEventListener('DOMContentLoaded', () => {
  const company=document.querySelector('[data-company-select]');
  const contact=document.querySelector('[data-contact-select]');
  if(company&&contact){
    const choices=[...contact.options].map(o=>o.cloneNode(true));
    function update(){const selected=contact.value;contact.replaceChildren(...choices.filter(o=>!o.value||o.dataset.company===company.value).map(o=>o.cloneNode(true)));contact.value=[...contact.options].some(o=>o.value===selected)?selected:'';}
    company.addEventListener('change',update);update();
  }
  const create=document.querySelector('[name="create_contact"]');
  if(create&&contact){contact.addEventListener('change',()=>{if(contact.value)create.checked=false;});create.addEventListener('change',()=>{if(create.checked)contact.value='';});}
  const newCompany=document.querySelector('[name="new_company_name"]');
  if(newCompany&&company){company.addEventListener('change',()=>{if(company.value)newCompany.value='';});newCompany.addEventListener('input',()=>{if(newCompany.value.trim()){company.value='';company.dispatchEvent(new Event('change'));}});}
  const stage=document.querySelector('select[name="stage"]');
  const actual=document.querySelector('[name="actual_close_date"]');
  if(stage&&actual){function updateActual(){const closed=['won','lost'].includes(stage.value);actual.disabled=!closed;if(!closed)actual.value='';}stage.addEventListener('change',updateActual);updateActual();}
});
