const instance = document.querySelector('#instance');
const windowSelect = document.querySelector('#window');
const canvas = document.querySelector('#map');
const colors = ['#006dc2', '#bf4c22', '#007c68', '#7849a3', '#886300'];
const labels = {balanced:'均衡分配', mean:'平均需求', risk:'需求与波动', trend:'趋势场景', local_search:'局部搜索'};
const format = new Intl.NumberFormat('zh-CN', {maximumFractionDigits:2, minimumFractionDigits:2});
let activeDepot = -1;

function draw() {
  const {depots, groups} = instances[instance.value];
  const points = [...depots, ...groups.flat()];
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const {width, height} = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
  const c = canvas.getContext('2d');
  c.scale(ratio, ratio);
  const dx = maxX - minX, dy = maxY - minY;
  const scale = Math.min((width - 55) / Math.max(dx, 1), (height - 55) / Math.max(dy, 1));
  const project = ([x,y]) => [width/2 + (x - (minX+maxX)/2)*scale, height/2 - (y - (minY+maxY)/2)*scale];
  c.strokeStyle = '#e1eaf3';
  for (let i = 1; i < 8; i++) {
    c.beginPath(); c.moveTo(width*i/8,0); c.lineTo(width*i/8,height);
    c.moveTo(0,height*i/8); c.lineTo(width,height*i/8); c.stroke();
  }
  groups.forEach((group, d) => {
    c.fillStyle = colors[d % colors.length]; c.globalAlpha = activeDepot === -1 || activeDepot === d ? .66 : .09;
    group.forEach(p => {
      const [x,y] = project(p);
      c.beginPath(); c.arc(x,y,group.length > 400 ? 1.6 : 2.4,0,2*Math.PI); c.fill();
    });
  });
  depots.forEach((p,d) => {
    const [x,y] = project(p);
    c.globalAlpha = activeDepot === -1 || activeDepot === d ? 1 : .35;
    c.fillStyle = colors[d % colors.length]; c.fillRect(x-5,y-5,10,10);
    c.strokeStyle = 'white'; c.strokeRect(x-5,y-5,10,10);
    c.font = 'bold 13px system-ui'; c.fillText(String(d),x+8,y-7);
  });
}

function renderFleet() {
  const data = instances[instance.value];
  const customers = data.groups.reduce((n,g) => n+g.length, 0);
  document.querySelector('#summary').innerHTML = [data.depots.length+' 个仓库', customers+' 位客户', data.caps.length+' 辆车', data.periods+' 个历史周期'].map(s => '<span>'+s+'</span>').join('');
  document.querySelector('#fleet').innerHTML = data.allocation.map((vehicles,d) => {
    const ids = [...vehicles].sort((a,b)=>a-b);
    const total = ids.reduce((n,v)=>n+data.caps[v],0);
    return '<tr><td><button type="button" data-depot="'+d+'" aria-pressed="'+(d===activeDepot)+'" style="color:'+colors[d % colors.length]+'">'+d+'</button></td><td>'+ids.length+'</td><td>'+total+'</td><td>'+ids.map(v=>v+'（'+data.caps[v]+'）').join('、')+'</td></tr>';
  }).join('');
  document.querySelector('#data-note').textContent = data.ignored ? '原始数据多出 '+data.ignored+' 行客户记录，当前按文件头读取，额外记录的归属待课程方确认。' : '';
}
function renderCosts() {
  const {windows} = instances[instance.value];
  const entry = windows[Number(windowSelect.value)];
  const scores = entry.costs;
  const aiLink = new URL(document.querySelector('#ai-link').href);
  aiLink.search = new URLSearchParams({instance: instance.value, window: windowSelect.value});
  document.querySelector('#ai-link').href = aiLink.toString();
  const max = Math.max(1,...Object.values(scores));
  document.querySelector('#periods').textContent = '训练使用第 1–'+entry.train_end_exclusive+' 期，验证第 '+entry.test_periods.map(x=>x+1).join('、')+' 期。';
  document.querySelector('#costs').innerHTML = Object.entries(labels).map(([name,label])=>'<div class="row"><span>'+label+'</span><div class="track"><div class="bar '+(name==='local_search'?'selected':'')+'" style="width:'+scores[name]/max*100+'%"></div></div><span class="value">'+format.format(scores[name])+'</span></div>').join('');
  const rounded = Object.values(scores).map(score => Math.round(score * 100));
  const minimum = Math.min(...rounded);
  const leaders = Object.entries(labels).filter(([key]) => Math.round(scores[key] * 100) === minimum).map(([, label]) => label);
  const best = '最低成本为 ' + format.format(minimum / 100) + '（' + leaders.join('、') + '）。';
  const diff = scores.local_search - scores.balanced;
  document.querySelector('#comparison').textContent = best + (Math.abs(diff)<1e-7 ? '局部搜索与均衡分配的估算成本持平。' : '局部搜索比均衡分配'+(diff<0?'低 ':'高 ')+format.format(Math.abs(diff))+'。');
}
function render() {
  activeDepot = -1;
  const {windows} = instances[instance.value];
  windowSelect.replaceChildren(...windows.map((w,i)=>new Option('窗口 '+(i+1)+' · 训练前 '+w.train_end_exclusive+' 期',i)));
  renderFleet(); renderCosts(); draw();
}
instance.replaceChildren(...Object.keys(instances).map(id=>new Option(id,id)));
instance.addEventListener('change',render);
windowSelect.addEventListener('change',renderCosts);
document.querySelector('#fleet').addEventListener('click', e => {
  const button = e.target.closest('button[data-depot]');
  if (!button) return;
  const d = Number(button.dataset.depot);
  activeDepot = activeDepot === d ? -1 : d;
  document.querySelectorAll('button[data-depot]').forEach(b => b.setAttribute('aria-pressed', Number(b.dataset.depot)===activeDepot));
  draw();
});
new ResizeObserver(draw).observe(canvas);
render();
