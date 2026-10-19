
// ===== 交互式地图 =====
var map = L.map('map').setView([30.72, 103.95], 8);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{
  attribution:'© OpenStreetMap contributors'
}).addTo(map);

var spots = [
  ['成都熊猫基地',30.7389,104.1457],
  ['宽窄巷子',30.6690,104.0610],
  ['武侯祠',30.6461,104.0497],
  ['锦里古街',30.6470,104.0490],
  ['杜甫草堂',30.6551,104.0290],
  ['文殊院',30.6820,104.0710],
  ['人民公园',30.6630,104.0560],
  ['春熙路/太古里',30.6530,104.0830],
  ['都江堰',30.9980,103.6140],
  ['青城山',30.9050,103.5650],
  ['乐山大佛',29.5460,103.7690],
  ['三星堆博物馆',30.9970,104.2960]
];
spots.forEach(function(s){
  L.marker([s[1],s[2]]).addTo(map)
    .bindPopup('<b>'+s[0]+'</b><br>'+s[1].toFixed(4)+', '+s[2].toFixed(4));
});

// ===== 预算记账 =====
var cats = ['往返大交通','住宿','景点门票','餐饮','市内/城际交通','购物及其他'];
var box = document.getElementById('budgetInputs');
cats.forEach(function(c){
  var row = document.createElement('div');
  row.className = 'budget-row';
  row.innerHTML = '<label>'+c+'</label><input type="number" class="amt" min="0" placeholder="¥/人" value="0">';
  box.appendChild(row);
});
function calc(){
  var amts = document.querySelectorAll('.amt');
  var sum = 0;
  amts.forEach(function(a){ sum += (parseFloat(a.value)||0); });
  document.getElementById('totalDisplay').textContent = '合计：¥' + sum.toFixed(0) + ' /人';
  var hint = document.getElementById('budgetHint');
  if(sum < 2500){ hint.textContent = '低于¥2500，还可适当加餐或升级体验。'; }
  else if(sum <= 5000){ hint.textContent = '✅ 在预算区间内。'; }
  else { hint.textContent = '已超¥5000，建议选廉航/经济型酒店控制成本。'; }
}
document.querySelectorAll('.amt').forEach(function(a){ a.addEventListener('input', calc); });
calc();
