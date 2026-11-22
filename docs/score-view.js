/* Published scoring results are kept separate from historical validation data. */
const scoreFormatter = new Intl.NumberFormat('zh-CN', {minimumFractionDigits:3,maximumFractionDigits:3});
function scoreModeActive() {
  return document.getElementById('result-mode').value === 'getscore';
}
function currentPublished() {
  return publishedResults[document.getElementById('instance').value];
}
function scoreSourceLabel(name, index=0) {
  const names = {'output':'原方案','score_output':'评分搜索',
    'score_benchmark':'组合搜索','score_optimization/allocations':'改进方案',
    'released':'上次正式提交','incumbent':'上次方案','reference':'原方案',
    'mean_greedy':'需求均值分配'};
  return names[name] || '候选方案 '+(index+1);
}
function publishedEntries() {
  return (currentPublished()?.candidates || []).filter(row=>Number.isFinite(row.median));
}
function renderResultContext() {
  const published = currentPublished();
  const scoring = scoreModeActive();
  document.getElementById('overview-validation').textContent = scoring ?
    '比较同一评分目标下的候选方案，独立复测后输出正式提交。' :
    '输出每个仓库的车辆清单，在后续验证期比较配送距离和未服务罚分。';
  const phases = scoring ? [
    ['读取历史需求','使用每位客户的全历史平均需求'],['搜索候选分配','跨仓移动、交换与车队重组'],
    ['独立复测选优','新建评分器，比较中位数与多数轮次'],['生成正式提交','答案、评分报告和网页同步']
  ] : [
    ['读取历史需求','识别仓库、客户和车辆载重'],['生成初始分配','均衡、均值、波动和趋势'],
    ['移动或交换车辆','按训练期估算成本筛选'],['验证分配策略','在后续周期比较成本']
  ];
  document.querySelector('.flow-list').innerHTML = phases.map(([title,note])=>'<li>'+title+'<small>'+note+'</small></li>').join('');
  document.getElementById('flow-explanation').textContent = scoring ?
    '搜索缓存只用于筛选。正式提交使用独立复测结果，并保留原方案作为对照；超时保留的分配明确标为待复测。' :
    '每个验证窗口先用较早的历史生成方案，再用后续周期检查效果。最终车辆清单以全部历史为取样范围。';
  document.getElementById('result-context').textContent = scoring ?
    '评分目标：全历史平均需求；未服务罚分为单程距离的 2 倍。成本越低越好。' :
    '历史验证：按各期需求计算；未服务罚分为单程距离的 4 倍。与评分目标分别展示。';
  document.getElementById('fleet-source').textContent = !scoring ?
    '历史模式：用全部历史生成的分配；窗口成本来自各自独立训练。' :
    published?.status === 'validated' ? '与正式提交文件一致；已完成 '+published.trials+' 次独立复测。' :
    published ? '当前为超时保留的合法分配，尚未完成本轮独立复测。' :
    '尚未生成正式提交；当前历史分配仅供参考。';
}
function renderScoreCosts() {
  const published = currentPublished();
  const entries = publishedEntries();
  document.getElementById('cost-heading').textContent = '评分方案的独立复测';
  document.getElementById('periods').textContent = published?.status === 'validated' ?
    '每仓 '+published.seconds_per_depot+' 秒 · '+published.trials+' 次独立求解 · 按中位数选优' :
    '本实例尚未完成独立复测，暂不展示评分。';
  const max = Math.max(1,...entries.map(row=>row.median));
  document.getElementById('bars').innerHTML = entries.map((row,i)=>
    '<div class="barrow"><span>'+scoreSourceLabel(row.name,i)+'</span><div class="track"><div class="fill '+
    (row.name===published.source?'local':'')+'" style="width:'+100*row.median/max+
    '%"></div></div><span class="amount">'+scoreFormatter.format(row.median)+'</span></div>').join('');
  const chosen = entries.find(row=>row.name===published?.source);
  document.getElementById('cost-result').textContent = chosen ?
    '正式提交采用'+scoreSourceLabel(chosen.name,entries.indexOf(chosen))+'，成本中位数 '+scoreFormatter.format(chosen.median)+
    '，复测范围 '+scoreFormatter.format(chosen.minimum)+'–'+scoreFormatter.format(chosen.maximum)+
    '。限时求解结果会波动；数值为兼容参考成本。' : '没有可用的完整复测结果。';
  const link = new URL('https://workyy-svg.github.io/multi-depot-vehicle-allocation/ai/');
  link.search = new URLSearchParams({instance:dataset.value,mode:'getscore'});
  document.getElementById('ai-link').href = link.href;
}
function renderScoreAnswer() {
  const published = currentPublished();
  const entries = publishedEntries();
  if (activeQuestion===3 || activeQuestion===4 || activeQuestion===6) return false;
  const chart = document.getElementById('ai-chart');
  const answer = document.getElementById('answer');
  if (!entries.length) {
    document.getElementById('ai-chart-title').textContent='等待独立复测';
    chart.textContent='';
    answer.textContent='当前实例尚无完整复测结果。';
    return true;
  }
  const selected = entries.find(row=>row.name===published.source);
  if (activeQuestion===5) {
    document.getElementById('ai-chart-title').textContent='评分方法';
    chart.textContent='使用全历史平均需求，车辆容量约束精确保留小数需求；距离和罚分按 1000 倍取整。';
    answer.textContent='未服务罚分为仓库到客户单程距离的 2 倍；每仓 '+published.seconds_per_depot+
      ' 秒，共独立求解 '+published.trials+' 次，以中位数并结合多数轮次比较选优。';
    return true;
  }
  const ranked = [...entries].sort((a,b)=>a.median-b.median);
  const max = Math.max(1,...ranked.map(row=>row.median));
  document.getElementById('ai-chart-title').textContent='候选方案的复测中位数';
  chart.innerHTML = ranked.map(row=>'<div class="barrow"><span>'+scoreSourceLabel(row.name,entries.indexOf(row))+
    '</span><div class="track"><div class="fill '+(row.name===published.source?'local':'')+
    '" style="width:'+100*row.median/max+'%"></div></div><span class="amount">'+scoreFormatter.format(row.median)+'</span></div>').join('');
  if (activeQuestion===0) {
    answer.textContent=scoreSourceLabel(ranked[0].name,entries.indexOf(ranked[0]))+'的复测中位数最低，为 '+scoreFormatter.format(ranked[0].median)+
      '。正式提交还要求相对保留方案在多数轮次中更好。';
  } else if (activeQuestion===1) {
    const original = entries.find(row=>row.name==='output');
    const difference = original && selected ? original.median-selected.median : null;
    answer.textContent=difference===null?'本轮没有原方案的完整对照数据。':
      '正式提交相对原方案的成本'+(difference>=0?'减少 ':'增加 ')+scoreFormatter.format(Math.abs(difference))+'。';
  } else {
    answer.textContent=ranked.map(row=>scoreSourceLabel(row.name,entries.indexOf(row))+' '+scoreFormatter.format(row.median)).join('；')+
      '。这些是同一目标下的独立复测结果。';
  }
  return true;
}
