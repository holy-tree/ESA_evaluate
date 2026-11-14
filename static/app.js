/* Time strings are never passed through Date: naive ESA values stay UTC. */
(() => {
  const data = JSON.parse(document.querySelector('#page-data').textContent);
  const form = document.querySelector('#evaluation-form');
  const rows = document.querySelector('#prediction-rows');
  const message = document.querySelector('#client-message');
  const identity = document.querySelector('#user-id');
  let dirty = false;
  function invalidate(clearUpload = false) {
    const result = document.querySelector('#evaluation-result');
    const report = document.querySelector('#validation-report');
    if (result) result.hidden = true;
    if (report) report.hidden = true;
    if (result || report) document.querySelector('#stale-notice').hidden = false;
    if (clearUpload) document.querySelector('#upload-token').value = '';
    window.dispatchEvent(new CustomEvent('esa:invalidate-chart'));
    dirty = true;
  }
  function addRow(row = {}) {
    const tr = document.createElement('tr');
    const channelCell = document.createElement('td');
    const select = document.createElement('select');
    select.name = 'channel'; select.setAttribute('aria-label', '通道');
    data.channels.forEach(channel => {
      const opt = new Option(channel, channel, false, channel === row.channel);
      select.add(opt);
    });
    channelCell.append(select); tr.append(channelCell);
    ['starttime', 'endtime'].forEach(name => {
      const td = document.createElement('td'); const input = document.createElement('input');
      input.name = name; input.value = row[name] || ''; input.placeholder = 'ISO-8601 · UTC';
      input.setAttribute('aria-label', name); td.append(input); tr.append(td);
    });
    const td = document.createElement('td'); const button = document.createElement('button');
    button.type = 'button'; button.className = 'delete-row quiet'; button.textContent = '删除';
    td.append(button); tr.append(td); rows.append(tr);
  }
  rows.addEventListener('click', e => {
    if (e.target.matches('.delete-row')) { e.target.closest('tr').remove(); invalidate(); }
  });
  rows.addEventListener('input', () => invalidate());
  rows.addEventListener('change', () => invalidate());
  document.querySelector('#add-row').addEventListener('click', () => { addRow(); invalidate(); });
  document.querySelector('#clear-rows').addEventListener('click', () => { rows.replaceChildren(); invalidate(); });
  document.querySelectorAll('.context-select').forEach(select => {
    select.addEventListener('change', () => {
      if ((dirty || rows.children.length || data.has_result || document.querySelector('#upload-token').value) &&
          !window.confirm('切换评测配置后，旧输入、CSV／JSON 校验与分数不再适用，将清空。继续吗？')) {
        select.value = select.dataset.previous; return;
      }
      const params = new URLSearchParams();
      ['mission', 'source', 'category'].forEach(name => params.set(name, document.querySelector(`#${name}`).value));
      params.set('mode', form.querySelector('[name=mode]:checked').value);
      if (identity.value.trim()) params.set('user_id', identity.value.trim());
      window.location.assign('/?' + params.toString());
    });
    select.dataset.previous = select.value;
  });
  document.querySelectorAll('.score-config').forEach(input => input.addEventListener('input', () => invalidate(true)));
  function identityHint() {
    const value = form.querySelector('[name=mode]:checked')?.value || 'csv';
    identity.required = value === 'manual';
    let text;
    if (identity.value.trim()) text = `本次使用填写的 ID：${identity.value.trim()}`;
    else if (value === 'manual') text = '手动提交需要填写用户 ID。';
    else {
      const filename = document.querySelector(`#prediction-${value}`).files[0]?.name || (document.querySelector('#upload-token').value ? data.upload_filename : '');
      text = filename ? `本文件默认 ID：${filename}（含扩展名）` : '选择文件后，文件名可自动作为本次 ID。';
    }
    document.querySelector('#identity-hint').textContent = text;
  }
  ['csv','json'].forEach(value => document.querySelector(`#prediction-${value}`).addEventListener('change', () => { invalidate(true); identityHint(); }));
  identity.addEventListener('input', () => { identityHint(); invalidate(); document.querySelectorAll('.best-row-current').forEach(row => row.classList.remove('best-row-current')); });
  function showMode(value) {
    document.querySelector('#manual-input').hidden = value !== 'manual';
    document.querySelector('#csv-input').hidden = value === 'manual';
    ['csv', 'json'].forEach(format => {
      const input = document.querySelector(`#prediction-${format}`);
      input.hidden = value !== format; input.disabled = value !== format;
    });
    document.querySelectorAll('[data-upload-format]').forEach(node => { node.hidden = node.dataset.uploadFormat !== value; });
    identityHint();
  }
  document.querySelectorAll('[name=mode]').forEach(radio => radio.addEventListener('change', () => {
    showMode(radio.value);
    document.querySelector('#prediction-csv').value = '';
    document.querySelector('#prediction-json').value = '';
    invalidate(true);
    identityHint();
  }));
  const initialMode = form.querySelector('[name=mode]:checked') || form.querySelector('[name=mode][value=csv]');
  initialMode.checked = true;
  showMode(initialMode.value);
  document.querySelector('#load-example').addEventListener('click', async () => {
    const params = new URLSearchParams({mission: document.querySelector('#mission').value,
      source: document.querySelector('#source').value, version: data.version});
    try {
      const response = await fetch('/example?' + params);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error);
      rows.replaceChildren(); body.rows.forEach(addRow); invalidate();
      message.hidden = true;
    } catch (error) { message.textContent = error.message; message.hidden = false; }
  });
})();
