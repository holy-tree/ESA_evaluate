/* Table-only sorting/filtering: never changes score scope, stored result or exports. */
(() => {
  function init() {
  const table = document.getElementById('channel-scores');
  if (!table || table.dataset.initialized === 'true') return;
  table.dataset.initialized = 'true';
  const $ = id => document.getElementById(id);
  const groups = [...table.querySelectorAll(':scope > tbody.channel-group')].map(body => ({body,
    channel: body.dataset.channel, original: Number(body.dataset.originalIndex), metrics: JSON.parse(body.dataset.metrics)}));
  const headings = [...table.querySelectorAll(':scope > thead th[data-key]')];
  const columns = new Map(headings.map(th => [th.dataset.key, th]));
  const filters = new Map();
  let sortKey = null, direction = 'none', editingKey = null;
  function value(row, key) { return key === 'channel' ? row.channel : row.metrics[key]; }
  function defined(v) { return typeof v === 'number' && Number.isFinite(v); }
  function matches(row) {
    for (const [key,filter] of filters) {
      const v = value(row,key);
      if (key === 'channel') { if (!v.toLocaleLowerCase().includes(filter.query.toLocaleLowerCase())) return false; }
      else {
        const has = defined(v);
        if (filter.state === 'missing') { if (has) return false; continue; }
        if (!has) { if (filter.state === 'defined' || filter.min !== null || filter.max !== null) return false; continue; }
        if (filter.min !== null && v < filter.min || filter.max !== null && v > filter.max) return false;
      }
    }
    return true;
  }
  function render() {
    const sorted = [...groups];
    if (sortKey && direction !== 'none') sorted.sort((a,b) => {
      const av=value(a,sortKey), bv=value(b,sortKey);
      let comparison;
      if (sortKey === 'channel') comparison = av.localeCompare(bv, 'zh-CN', {numeric:true});
      else {
        const ad=defined(av), bd=defined(bv);
        if (ad !== bd) return ad ? -1 : 1; // null always last, in either direction
        comparison = ad ? av-bv : 0;
      }
      return (direction === 'descending' ? -comparison : comparison) || a.original-b.original;
    });
    else sorted.sort((a,b) => a.original-b.original);
    let visible=0;
    for (const row of sorted) {
      row.body.hidden=!matches(row); if (!row.body.hidden) ++visible;
      table.insertBefore(row.body,$('channel-table-empty'));
    }
    for (const th of headings) {
      const state=th.dataset.key === sortKey ? direction : 'none';
      th.setAttribute('aria-sort',state);
      th.querySelector('.channel-sort-icon').textContent=state === 'ascending' ? '↑' : state === 'descending' ? '↓' : '↕';
      const label=state === 'ascending' ? '升序' : state === 'descending' ? '降序' : '默认';
      th.querySelector('.channel-sort').title=`${th.dataset.label}：${label}（点击切换）`;
      th.querySelector('.channel-filter-button').classList.toggle('is-filtered',filters.has(th.dataset.key));
    }
    const label=sortKey ? `${columns.get(sortKey).dataset.label}${direction === 'ascending' ? '升序' : '降序'}` : '默认顺序';
    $('channel-table-status').textContent=`显示 ${visible} / ${groups.length} 个通道 · ${label}`;
    $('channel-table-empty').hidden=visible !== 0;
    table.dataset.sortKey=sortKey || ''; table.dataset.sortDirection=direction;
  }
  function closeFilter() {
    $('channel-filter-panel').hidden=true;
    headings.forEach(th=>th.querySelector('.channel-filter-button').setAttribute('aria-expanded','false'));
    editingKey=null;
  }
  function openFilter(key) {
    if (editingKey === key && !$('channel-filter-panel').hidden) { closeFilter(); return; }
    closeFilter(); editingKey=key;
    const th=columns.get(key), filter=filters.get(key);
    $('channel-filter-title').textContent=`筛选 · ${th.dataset.label}`;
    $('channel-filter-text-label').hidden=key !== 'channel';
    $('channel-filter-numeric').hidden=key === 'channel';
    $('channel-filter-text').value=filter?.query || '';
    $('channel-filter-min').value=filter?.min ?? '';
    $('channel-filter-max').value=filter?.max ?? '';
    $('channel-filter-state').value=filter?.state || 'all';
    $('channel-filter-error').hidden=true;
    $('channel-filter-panel').hidden=false;
    th.querySelector('.channel-filter-button').setAttribute('aria-expanded','true');
    syncMissing();
    $(key === 'channel' ? 'channel-filter-text' : 'channel-filter-min').focus();
  }
  function syncMissing() {
    const missing=$('channel-filter-state').value === 'missing';
    ['channel-filter-min','channel-filter-max'].forEach(id=>{$(id).disabled=missing;});
  }
  table.querySelector(':scope > thead').addEventListener('click',event=>{
    const th=event.target.closest('th[data-key]'); if (!th) return;
    const key=th.dataset.key;
    if (event.target.closest('.channel-filter-button')) { openFilter(key); return; }
    closeFilter();
    direction=sortKey !== key || direction === 'none' ? 'ascending' : direction === 'ascending' ? 'descending' : 'none';
    sortKey=direction === 'none' ? null : key;
    render();
  });
  table.addEventListener('click',event=>{
    const row=event.target.closest('tr.channel-summary'); if (!row || !table.contains(row)) return;
    const toggle=row.querySelector('.channel-toggle'), details=$(toggle.getAttribute('aria-controls'));
    const expanded=toggle.getAttribute('aria-expanded') !== 'true';
    toggle.setAttribute('aria-expanded',String(expanded)); details.hidden=!expanded;
    toggle.setAttribute('aria-label',`${expanded ? '收起' : '展开'} ${row.dataset.channel} 的事件明细`);
    row.classList.toggle('is-expanded',expanded);
  });
  $('channel-search').addEventListener('input',event=>{
    const query=event.target.value.trim(); if (query) filters.set('channel',{query}); else filters.delete('channel');
    if (editingKey === 'channel') $('channel-filter-text').value=query;
    render();
  });
  $('channel-filter-state').addEventListener('change',syncMissing);
  $('channel-filter-apply').addEventListener('click',()=>{
    if (!editingKey) return;
    if (editingKey === 'channel') {
      const query=$('channel-filter-text').value.trim(); $('channel-search').value=query;
      if (query) filters.set(editingKey,{query}); else filters.delete(editingKey);
    } else {
      const state=$('channel-filter-state').value;
      const minimum=$('channel-filter-min').value, maximum=$('channel-filter-max').value;
      const min=state === 'missing' || minimum === '' ? null : Number(minimum);
      const max=state === 'missing' || maximum === '' ? null : Number(maximum);
      if (min !== null && !Number.isFinite(min) || max !== null && !Number.isFinite(max) || min !== null && max !== null && min > max) {
        $('channel-filter-error').textContent='数值范围无效，最小值不能大于最大值。'; $('channel-filter-error').hidden=false; return;
      }
      if (state === 'all' && min === null && max === null) filters.delete(editingKey);
      else filters.set(editingKey,{state,min,max});
    }
    closeFilter(); render();
  });
  $('channel-filter-clear').addEventListener('click',()=>{
    if (editingKey === 'channel') $('channel-search').value='';
    filters.delete(editingKey); closeFilter(); render();
  });
  $('channel-filter-close').addEventListener('click',closeFilter);
  $('channel-filter-panel').addEventListener('keydown',event=>{
    if (event.key === 'Escape') closeFilter();
    if (event.key === 'Enter' && event.target.matches('input')) { event.preventDefault(); $('channel-filter-apply').click(); }
  });
  $('channel-table-reset').addEventListener('click',()=>{
    filters.clear(); sortKey=null; direction='none'; $('channel-search').value=''; closeFilter(); render();
  });
  render();
  }
  window.ESAChannelTable = {init};
  init();
})();
