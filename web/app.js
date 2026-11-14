const state = {context: null, current: null, models: [], activeModel: null, assignData: null};
const element = identifier => document.getElementById(identifier);

async function request(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败');
  return value;
}

function message(value, error = false) {
  element('status').textContent = value;
  element('status').className = error ? 'error' : '';
}

async function guarded(action) {
  try { await action(); } catch (error) { message(error.message, true); }
}

function selected() {
  const selection = {};
  for (const control of state.context.product.options) selection[control[0]] = element('select-' + control[0]).value;
  return {revision: element('revision').value, selection};
}

function renderControls() {
  const revision = element('revision').value || state.context.default_revision;
  element('revision').replaceChildren();
  for (const dataset of state.context.datasets) {
    const option = document.createElement('option');
    option.value = dataset.revision;
    option.textContent = dataset.label + ' · ' + dataset.revision.slice(0, 9);
    element('revision').append(option);
  }
  element('revision').value = revision;
  if (!element('revision').value) element('revision').value = state.context.default_revision;
  if (element('controls').children.length) return;
  for (const [key, labelText, choices] of state.context.product.options) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const select = document.createElement('select');
    select.id = 'select-' + key;
    for (const choice of choices) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      select.append(option);
    }
    select.value = state.context.product.selection[key];
    label.append(select);
    element('controls').append(label);
  }
}

function displayValue(value) {
  if (value === null || value === undefined) return '未观测';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4);
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

function drawChart(series) {
  const canvas = element('chart');
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  const values = series.filter(point => typeof point.y === 'number' && Number.isFinite(point.y));
  context.font = '12px sans-serif';
  if (!values.length) { context.fillText('该范围没有可定量的描述值', 30, 50); return; }
  const low = Math.min(0, ...values.map(point => point.y));
  const high = Math.max(1, ...values.map(point => point.y));
  const scale = value => 250 - (value - low) / (high - low || 1) * 210;
  context.strokeStyle = '#90a5ad';
  context.beginPath(); context.moveTo(45, 20); context.lineTo(45, 250); context.lineTo(940, 250); context.stroke();
  context.fillStyle = '#0a7168';
  const scatter = values.every(point => typeof point.x === 'number');
  const minimumX = scatter ? Math.min(...values.map(point => point.x)) : 0;
  const maximumX = scatter ? Math.max(...values.map(point => point.x)) : 1;
  values.forEach((point, index) => {
    const position = scatter ? 60 + (point.x - minimumX) / (maximumX - minimumX || 1) * 850 : 55 + index * 875 / values.length;
    if (scatter) {
      context.beginPath(); context.arc(position, scale(point.y), 3.5, 0, Math.PI * 2); context.fill();
    } else {
      context.fillRect(position, Math.min(scale(point.y), scale(0)), Math.max(3, 800 / values.length), Math.max(1, Math.abs(scale(point.y) - scale(0))));
    }
    if (values.length < 12) context.fillText(point.label, position, 275);
  });
  context.fillStyle = '#29434e';
  context.fillText(high.toFixed(2), 0, 45); context.fillText(low.toFixed(2), 0, 250);
  context.fillText(scatter ? '所选资料的数值坐标（仅为原始描述）' : '按表中顺序展示描述值；缺测不绘制为零', 50, 20);
}

function render(result, heading) {
  state.current = result;
  element('summary').replaceChildren();
  for (const [label, value] of Object.entries(result.view.summary)) {
    const card = document.createElement('article');
    const title = document.createElement('span'); title.textContent = label;
    const amount = document.createElement('strong'); amount.textContent = displayValue(value);
    card.append(title, amount); element('summary').append(card);
  }
  element('notes').replaceChildren();
  for (const note of result.view.notes) {
    const paragraph = document.createElement('p'); paragraph.textContent = note; element('notes').append(paragraph);
  }
  const rows = result.view.rows;
  const fields = rows.length ? Object.keys(rows[0]) : [];
  const header = document.createElement('tr');
  for (const field of fields) { const cell = document.createElement('th'); cell.textContent = field; header.append(cell); }
  element('columns').replaceChildren(header); element('rows').replaceChildren();
  for (const row of rows) {
    const line = document.createElement('tr'); line.tabIndex = 0;
    for (const field of fields) { const cell = document.createElement('td'); cell.textContent = displayValue(row[field]); line.append(cell); }
    line.onclick = () => { element('details').textContent = JSON.stringify(row, null, 2); };
    line.onkeydown = event => { if (event.key === 'Enter') line.click(); };
    element('rows').append(line);
  }
  drawChart(result.view.series);
  element('details').textContent = JSON.stringify({revision: result.revision, selection: result.selection,
    history: result.view.history || null}, null, 2);
  message((heading || '已更新描述') + ' · 数据 ' + result.revision.slice(0, 12));
}

async function refresh() {
  state.context = await request('/api/context');
  renderControls();
  element('archive').replaceChildren();
  for (const notebook of state.context.notebooks) {
    const button = document.createElement('button'); button.textContent = notebook.title + ' #' + notebook.id;
    button.onclick = () => guarded(async () => {
      const saved = await request('/api/notebooks/' + notebook.id);
      element('revision').value = saved.revision;
      for (const [key, value] of Object.entries(saved.selection)) {
        const control = element('select-' + key); if (control) control.value = value;
      }
      render(saved.result, '已读取保存的描述，不随新数据变化');
    });
    element('archive').append(button);
  }
  await refreshModels();
}

async function refreshModels(selectId) {
  state.models = await request('/api/models');
  const select = element('model-select');
  const current = selectId || select.value || (state.activeModel && state.activeModel.model_id) || (state.models[0] && state.models[0].model_id);
  select.replaceChildren();
  if (!state.models.length) {
    const option = document.createElement('option');
    option.textContent = '暂无已发布分型'; option.value = '';
    select.append(option);
    element('typology-status').textContent = '尚未训练分型。训练结果按学习产物指纹版本化，人工命名另行留痕。';
    state.activeModel = null;
    element('cluster-cards').replaceChildren();
    element('k-selection').replaceChildren();
    element('naming-box').replaceChildren();
    return;
  }
  for (const model of state.models) {
    const option = document.createElement('option');
    option.value = model.model_id;
    option.textContent = model.model_id.slice(0, 10) + ' · 数据 ' + model.revision.slice(0, 8) + ' · ' + model.created_at.slice(0, 10);
    select.append(option);
  }
  const targetId = current && state.models.some(model => model.model_id === current)
    ? current : state.models[0].model_id;
  select.value = targetId;
  await loadModel(targetId);
}

async function loadModel(modelId) {
  if (!modelId) return;
  state.activeModel = await request('/api/models/' + encodeURIComponent(modelId));
  renderTypology(state.activeModel);
}

function renderTypology(model) {
  const naming = model.naming || {version: 0, names: {}};
  element('typology-status').textContent =
    '分型版本 ' + model.model_id.slice(0, 12) + '（共 ' + model.clusters.length + ' 类，' +
    model.training.household_count + ' 户）· 人工命名版本 v' + naming.version +
    (naming.note ? ' · ' + naming.note : ' · 尚未人工命名，显示为临时类号');
  renderKSelection(model);
  renderClusterCards(model, naming.names);
  renderNamingBox(model, naming);
}

function renderKSelection(model) {
  const box = element('k-selection');
  box.replaceChildren();
  const selection = model.training.k_selection;
  const heading = document.createElement('h3');
  heading.textContent = '类数选择依据（推荐 k=' + selection.recommended_k + '）';
  box.append(heading);
  const criterion = document.createElement('p');
  criterion.className = 'hint';
  criterion.textContent = Object.values(selection.criterion).join('；');
  box.append(criterion);
  const table = document.createElement('table');
  table.className = 'mini-table';
  const thead = document.createElement('thead');
  const head = document.createElement('tr');
  for (const label of ['类数 k', '轮廓系数', '自助ARI均值', 'ARI最小值', '共分配率', '最小类规模', '是否稳定']) {
    const cell = document.createElement('th'); cell.textContent = label; head.append(cell);
  }
  thead.append(head);
  table.append(thead);
  const tbody = document.createElement('tbody');
  for (const candidate of selection.candidates) {
    const row = document.createElement('tr');
    if (candidate.k === selection.recommended_k) row.className = 'recommended';
    const values = [candidate.k, displayValue(candidate.silhouette), displayValue(candidate.bootstrap_ari_mean),
      displayValue(candidate.bootstrap_ari_min), displayValue(candidate.coassignment_mean),
      candidate.smallest_cluster, candidate.stable ? '稳定' : '不稳定'];
    for (const value of values) {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    }
    tbody.append(row);
  }
  table.append(tbody);
  box.append(table);
  if (!selection.all_candidates_stable) {
    const warning = document.createElement('p');
    warning.className = 'warning';
    warning.textContent = '提示：并非所有候选类数都通过稳定性阈值；未通过的类数保留在表中供复核，推荐结果只取稳定且轮廓最优者。';
    box.append(warning);
  }
}

function renderClusterCards(model, names) {
  const box = element('cluster-cards');
  box.replaceChildren();
  for (const cluster of model.clusters) {
    const card = document.createElement('article');
    card.className = 'cluster-card';
    const title = document.createElement('h3');
    title.textContent = '类 ' + cluster.cluster_id + (names[cluster.cluster_id] ? ' · ' + names[cluster.cluster_id] : '（待人工命名）');
    card.append(title);
    const path = cluster.representative_path;
    const rep = document.createElement('p');
    rep.className = 'path';
    rep.textContent = '代表路径（medoid 户 ' + cluster.medoid_household + '）：' +
      path.service_sequence.join(' → ') + '；接触日 ' + JSON.stringify(path.event_days) +
      '；观察窗 ' + path.window_length + ' 天' + (path.uncovered_days ? '；未观测 ' + path.uncovered_days + ' 天' : '；无覆盖缺口') +
      (path.prior_history ? '；上线前已有服务' : '');
    card.append(rep);
    const variation = cluster.within_group_variation;
    const detail = document.createElement('dl');
    const entries = [
      ['户数', cluster.size],
      ['事件数范围', variation.event_count_min + '–' + variation.event_count_max],
      ['观察窗范围', variation.window_length_min + '–' + variation.window_length_max + ' 天'],
      ['相邻间隔（天）', variation.gap_days_min === null ? '无间隔（仅单次或两次接触）' : variation.gap_days_min + '–' + variation.gap_days_max],
      ['到原型距离（均值/最大）', variation.distance_to_medoid_mean + ' / ' + variation.distance_to_medoid_max],
      ['登记组分布', JSON.stringify(variation.cohorts)],
      ['渠道分布', JSON.stringify(variation.channels)],
      ['上线前已使用服务户数', variation.prior_history_households],
      ['含覆盖缺口户数', variation.households_with_uncovered_gap],
      ['重复上报条数', variation.duplicate_reports],
    ];
    for (const [label, value] of entries) {
      const term = document.createElement('dt'); term.textContent = label;
      const desc = document.createElement('dd'); desc.textContent = value;
      detail.append(term, desc);
    }
    card.append(detail);
    const members = document.createElement('p');
    members.className = 'hint';
    members.textContent = '组内户：' + cluster.member_households.join(', ');
    card.append(members);
    box.append(card);
  }
}

function renderNamingBox(model, naming) {
  const box = element('naming-box');
  box.replaceChildren();
  const heading = document.createElement('h3');
  heading.textContent = '人工命名（当前 v' + naming.version + '，改名会追加新版本，不覆盖历史）';
  box.append(heading);
  for (const cluster of model.clusters) {
    const label = document.createElement('label');
    label.className = 'naming-row';
    label.textContent = '类 ' + cluster.cluster_id + ' 名称';
    const input = document.createElement('input');
    input.dataset.cluster = String(cluster.cluster_id);
    input.maxLength = 40;
    input.value = naming.names[cluster.cluster_id] || '';
    input.placeholder = '给接访员看的名称';
    label.append(input);
    box.append(label);
  }
  const note = document.createElement('label');
  note.className = 'naming-row';
  note.textContent = '命名说明';
  const noteInput = document.createElement('input');
  noteInput.id = 'naming-note';
  noteInput.maxLength = 600;
  noteInput.value = naming.note || '';
  note.append(noteInput);
  box.append(note);
  const button = document.createElement('button');
  button.id = 'save-names';
  button.textContent = '保存为新的命名版本';
  button.onclick = () => guarded(async () => {
    const names = {};
    for (const input of element('naming-box').querySelectorAll('input[data-cluster]')) {
      if (input.value.trim()) names[input.dataset.cluster] = input.value.trim();
    }
    const saved = await request('/api/models/name', {
      model_id: state.activeModel.model_id, names, note: noteInput.value
    });
    message('已保存命名版本 v' + saved.naming.version);
    await loadModel(state.activeModel.model_id);
  });
  box.append(button);
}

element('model-select').onchange = event => guarded(() => loadModel(event.target.value));

element('train').onclick = () => guarded(async () => {
  const params = {bootstraps: Number(element('train-bootstraps').value) || 40};
  const kValue = element('train-k').value.trim();
  if (kValue) params.k = Number(kValue);
  element('typology-status').textContent = '正在训练：距离矩阵、PAM 与固定种子自助验证可能需要数秒…';
  const trained = await request('/api/models/train', {revision: element('revision').value, params});
  message('分型已学习并发布为版本 ' + trained.model_id.slice(0, 12) + '；旧版本名称未改动');
  await refreshModels(trained.model_id);
});

element('show-stability').onclick = () => guarded(async () => {
  if (!state.activeModel) throw new Error('请先选择已发布分型');
  const report = await request('/api/models/' + encodeURIComponent(state.activeModel.model_id) + '/stability');
  element('details').textContent = JSON.stringify(report, null, 2);
  message('稳定性验证报告已展示在“记录与依据”区');
});

element('show-names').onclick = () => guarded(async () => {
  if (!state.activeModel) throw new Error('请先选择已发布分型');
  const history = await request('/api/models/' + encodeURIComponent(state.activeModel.model_id) + '/names');
  element('details').textContent = JSON.stringify(history, null, 2);
  message('命名版本历史已展示在“记录与依据”区');
});

element('assign-file').onchange = event => guarded(async () => {
  const file = event.target.files[0];
  if (!file) { state.assignData = null; return; }
  state.assignData = JSON.parse(await file.text());
  message('已读取待归类资料：' + file.name + '（提交时由服务端完整校验并版本化）');
});

element('assign').onclick = () => guarded(async () => {
  if (!state.activeModel) throw new Error('请先选择已发布分型');
  const body = {
    model_id: state.activeModel.model_id,
    household: element('assign-household').value.trim() || null,
  };
  if (state.assignData) {
    body.data = state.assignData;
    body.label = '待归类资料 ' + new Date().toISOString().slice(0, 10);
  }
  const outcome = await request('/api/models/assign', body);
  renderAssignment(outcome);
});

function renderAssignment(outcome) {
  const box = element('assign-result');
  box.replaceChildren();
  const meta = document.createElement('p');
  meta.className = 'hint';
  meta.textContent = '使用分型 ' + outcome.model_id.slice(0, 10) + '（命名 v' + outcome.naming_version +
    '）· 资料版本 ' + outcome.data_revision.slice(0, 10);
  box.append(meta);
  const rows = Array.isArray(outcome.result) ? outcome.result : [outcome.result];
  const names = (state.activeModel && state.activeModel.naming.names) || {};
  for (const row of rows) {
    const card = document.createElement('article');
    card.className = 'assign-card ' + (row.status === 'rejected' ? 'rejected' : 'assigned');
    const title = document.createElement('h3');
    const label = row.cluster_id === null ? '不适合归类' :
      '类 ' + row.cluster_id + (names[row.cluster_id] ? ' · ' + names[row.cluster_id] : '');
    title.textContent = row.household + '：' + label;
    card.append(title);
    const basis = document.createElement('p');
    basis.textContent = '最近类 ' + row.nearest_cluster_id + '，距离 ' + row.distance_to_cluster +
      '（序列 ' + row.distance_components.sequence + ' / 节奏 ' + row.distance_components.cadence +
      ' / 结构 ' + row.distance_components.structure + '）；次近类 ' +
      (row.second_cluster_id === null ? '无' : row.second_cluster_id + '，距离 ' + row.second_distance) +
      '；观察事件 ' + row.observed_events + ' 次，观察窗 ' + row.window_length + ' 天';
    card.append(basis);
    if (row.reject_reasons.length) {
      const list = document.createElement('ul');
      list.className = 'warning';
      for (const reason of row.reject_reasons) {
        const item = document.createElement('li');
        item.textContent = reason.code + '：' + reason.detail;
        list.append(item);
      }
      card.append(list);
    }
    const similar = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = '为什么接近：最相似的 ' + row.similar_households.length + ' 户（含距离分解）';
    similar.append(summary);
    for (const neighbor of row.similar_households) {
      const line = document.createElement('p');
      line.className = 'hint';
      line.textContent = neighbor.household + ' 距离 ' + neighbor.distance +
        '（序列 ' + neighbor.components.sequence + ' / 节奏 ' + neighbor.components.cadence +
        ' / 结构 ' + neighbor.components.structure + '）';
      similar.append(line);
    }
    card.append(similar);
    box.append(card);
  }
  message('归类完成：' + rows.length + ' 户，其中 ' +
    rows.filter(row => row.status === 'rejected').length + ' 户不适合归类');
}


element('inspect').onclick = () => guarded(async () => render(await request('/api/explore', selected())));
element('save').onclick = () => guarded(async () => {
  const saved = await request('/api/notebooks', {...selected(), title: element('title').value});
  await refresh(); render(saved.result, '已保存记录 #' + saved.id);
});
element('raw').onclick = () => guarded(async () => {
  const revision = state.current ? state.current.revision : element('revision').value;
  element('details').textContent = JSON.stringify(await request('/api/data?revision=' + encodeURIComponent(revision)), null, 2);
});
element('upload').onchange = () => guarded(async () => {
  const file = element('upload').files[0]; if (!file) return;
  const data = JSON.parse(await file.text());
  const uploaded = await request('/api/datasets', {label: file.name, data});
  await refresh(); element('revision').value = uploaded.revision;
  render(await request('/api/explore', selected()), '已导入独立数据版本');
});
element('export').onclick = () => guarded(async () => {
  if (!state.current) throw new Error('请先查看一个范围');
  const data = await request('/api/data?revision=' + encodeURIComponent(state.current.revision));
  const blob = new Blob([JSON.stringify({description: state.current, data}, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'analysis-' + state.current.revision.slice(0, 12) + '.json'; link.click();
  URL.revokeObjectURL(url);
});
guarded(async () => { await refresh(); render(await request('/api/explore', selected())); });
