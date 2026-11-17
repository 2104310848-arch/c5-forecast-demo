const byId = id => document.getElementById(id);
const formControls = ['daily', 'events', 'target', 'run'].map(byId);
const currency = n => new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(n);
const percent = n => n === null ? '暂无数据' : (n * 100).toFixed(2) + '%';
let worker;
let download;
function show({summary: s, backtest, forecast, zip}) {
  byId('completion').textContent = s.completion_pct.toFixed(2) + '%';
  byId('crange').textContent = '参考区间 ' + s.completion_lower_pct.toFixed(2) + '%–' + s.completion_upper_pct.toFixed(2) + '%';
  byId('income').textContent = '¥ ' + currency(s.forecast90);
  byId('range').textContent = '累计参考区间 ¥' + currency(s.lower90) + '–' + currency(s.upper90);
  byId('history').textContent = '¥ ' + currency(s.history);
  const t = s.tests.total_ape;
  const p = t.p_two_sided === null ? '暂无数据' : t.p_two_sided.toFixed(4);
  byId('evidence').textContent = s.backtest_windows + '个不重叠90天窗口。累计误差：模型 ' + percent(t.model_mean)
    + '，基线 ' + percent(t.baseline_mean) + '，双侧 p=' + p
    + '。原始区间覆盖 ' + percent(s.coverage90) + '，扩宽后顺序回测覆盖 '
    + percent(s.calibrated_coverage) + '（' + s.calibrated_windows + '窗）。区间证据有限。';
  const rows = byId('rows');
  rows.replaceChildren();
  for (const row of backtest) {
    const values = ['D' + row.test_start + '–D' + row.test_end, ...['actual', 'model_total', 'baseline_total'].map(k => currency(row[k])),
      percent(row.model_total_ape), percent(row.baseline_total_ape)];
    const tr = rows.insertRow();
    for (const value of values) tr.insertCell().textContent = value;
  }
  const ns = 'http://www.w3.org/2000/svg';
  const chart = byId('chart');
  chart.replaceChildren();
  const max = Math.max(1, ...forecast.map(r => r.point));
  forecast.forEach((r, i) => {
    if (!r.point) return;
    const bar = document.createElementNS(ns, 'rect');
    const height = 120 * r.point / max;
    for (const [k, v] of Object.entries({x: i * 11 + 1, y: 145 - height, width: 7, height, fill: '#087b80'})) bar.setAttribute(k, v);
    const title = document.createElementNS(ns, 'title');
    title.textContent = r.date + ': ' + currency(r.point) + '元';
    bar.append(title);
    chart.append(bar);
  });
  byId('eventsText').textContent = 'F001–F090 · ' + s.events + '个计划活动日 · 单次期望 ¥' + currency(s.model.slot_mean);
  download = zip;
  byId('results').hidden = false;
}
for (const control of formControls.slice(0, 3)) control.addEventListener('change', () => {
  byId('results').hidden = true;
  download = null;
  byId('status').textContent = '输入已更新，请重新运行';
});
byId('run').onclick = async () => {
  const files = ['daily', 'events'].map(id => byId(id).files[0]);
  const target = Number(byId('target').value);
  if (files.some(f => !f) || !Number.isFinite(target) || target <= 0) {
    byId('status').textContent = '请选择两份 CSV，并输入正数目标。'; return;
  }
  if (files.some(f => f.size > 5 * 1024 * 1024)) {
    byId('status').textContent = '每份 CSV 最大 5 MB。'; return;
  }
  byId('results').hidden = true;
  formControls.forEach(c => c.disabled = true);
  byId('status').textContent = worker ? '正在计算…' : '正在加载计算环境，首次运行可能需要较久…';
  try {
    worker ??= new Worker('worker.js');
    const [daily, events] = await Promise.all(files.map(f => f.text()));
    const data = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        worker.terminate(); worker = null; reject(Error('加载或计算超时，请检查网络后重试。'));
      }, 120000);
      worker.onmessage = ({data}) => {
        clearTimeout(timeout);
        data.error ? reject(Error(data.error)) : resolve(data.result);
      };
      worker.onerror = () => {
        clearTimeout(timeout); worker.terminate(); worker = null;
        reject(Error('浏览器未能启动计算环境，请重试。'));
      };
      worker.postMessage({daily, events, target});
    });
    show(data);
    byId('status').textContent = '计算完成，可下载结果';
  } catch (error) {
    byId('status').textContent = '运行失败：' + error.message;
  } finally { formControls.forEach(c => c.disabled = false); }
};
byId('download').onclick = () => {
  if (!download) return;
  const address = URL.createObjectURL(new Blob([Uint8Array.from(atob(download), c => c.charCodeAt(0))], {type:'application/zip'}));
  const link = document.createElement('a');
  link.href = address; link.download = 'C5-Pages-results.zip'; link.click();
  setTimeout(() => URL.revokeObjectURL(address), 1000);
};
