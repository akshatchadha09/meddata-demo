const $ = id => document.getElementById(id);
const tokenKey = 'MedData_token';
let currentUser = null;
let cache = { phcs: [], medicines: [], recs: [], conditions: [] };

const API_BASE = window.location.origin;
const api = async (path, opts = {}) => {
  opts.headers = {...(opts.headers || {})};
  const token = localStorage.getItem(tokenKey);
  if (token) opts.headers.Authorization = `Bearer ${token}`;
  let r;
  try { opts.credentials = 'same-origin'; r = await fetch(`${API_BASE}${path}`, opts); }
  catch (err) { throw new Error('MedData server is not reachable. Please start run_demo.bat and open http://127.0.0.1:8000'); }
  if (r.status === 401) {
    localStorage.removeItem(tokenKey); currentUser = null;
    if (location.pathname !== '/') window.location.replace('/');
    throw new Error('Session expired — please sign in again');
  }
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try { const d = await r.json(); msg = d.detail || msg; } catch(e) {}
    throw new Error(msg);
  }
  return r.json();
};

const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roleAccess = {
  GOVERNMENT:['overview','operations','planning','supply','emergency','federated','governance'],
  DISTRICT:['overview','operations','planning','supply','emergency','federated','governance'],
  PHC:['operations','supply'],
  SUPPLIER:['overview','planning','supply']
};
const pageMeta = {
  overview:['Overview','A single operating view for care delivery, resources, supply and preparedness.'],
  operations:['PHC Operations','Record encounters, update capacity and keep medicine inventory aligned with demand.'],
  planning:['AI Planning','Forecast demand, quantify procurement gaps and coordinate internal redistribution.'],
  supply:['Supply Chain','Trace medicine batches and maintain an auditable chain of custody.'],
  emergency:['Emergency Readiness','Stress-test disease surges before shortages and capacity pressure escalate.'],
  federated:['BRICS Federation','Demonstrate shared predictive modelling without exchanging raw patient records.'],
  governance:['Governance','Make access, verification, auditability and data-sharing boundaries visible.']
};

function showLogin(){ window.location.replace('/'); }
function showApp(){ $('appShell')?.removeAttribute('hidden'); }
function notify(message, kind='info'){
  const t=$('toast'); t.textContent=message; t.className=`toast show ${kind}`;
  clearTimeout(window.__toastTimer); window.__toastTimer=setTimeout(()=>t.className='toast',2800);
}
function setPage(id){
  if(!roleAccess[currentUser?.role || 'GOVERNMENT']?.includes(id)) return;
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active-page'));
  document.querySelectorAll('.nav').forEach(n=>n.classList.remove('active'));
  $(id).classList.add('active-page');
  document.querySelector(`.nav[data-section="${id}"]`)?.classList.add('active');
  $('pageTitle').textContent=pageMeta[id][0]; $('pageSubtitle').textContent=pageMeta[id][1];
  $('searchResults').hidden=true;
}
function configureRole(){
  document.querySelectorAll('.nav').forEach(n=>{
    const allowed=roleAccess[currentUser.role]?.includes(n.dataset.section);
    n.style.display=allowed?'flex':'none';
  });
  $('userBadge').textContent=`${currentUser.role} · ${currentUser.name}`;
  if(currentUser.role==='PHC'){
    $('patientPhc').disabled=true; $('facilityPhc').disabled=true;
  }
  setPage(roleAccess[currentUser.role][0]);
}
async function login(email,password){
  // Compatibility helper: authenticated navigation is now handled server-side.
  const form=document.createElement('form'); form.method='POST'; form.action='/web-login'; form.style.display='none';
  for(const [name,value] of [['email',email||''],['password',password||'']]){ const input=document.createElement('input'); input.name=name; input.value=value; form.appendChild(input); }
  document.body.appendChild(form); form.submit();
}

async function bootstrap(){
  try{
    const h=await fetch(`${API_BASE}/api/health`,{cache:'no-store',credentials:'same-origin'});
    if(!h.ok) throw new Error('MedData server error');
  }catch(e){ window.location.replace('/'); return; }
  try{
    currentUser=await api('/api/me');
    configureRole();
    showApp();
    await loadAll();
    notify(`Welcome, ${currentUser.name}`,'success');
  }catch(e){
    if(!location.pathname.startsWith('/')) window.location.replace('/');
  }
}

async function logout(){
  try{ await fetch(`${API_BASE}/api/logout`,{method:'POST',credentials:'same-origin',headers:{'Cache-Control':'no-cache'}}); }catch(e){}
  localStorage.removeItem(tokenKey); currentUser=null; window.location.replace('/');
}

async function loadAll(){
  if(!currentUser) return;
  try{
    const allowed=roleAccess[currentUser.role]||[];
    const requests={phcs:api('/api/phcs')};
    if(allowed.includes('overview')){
      requests.dashboard=api('/api/dashboard');
      requests.conditions=api('/api/conditions');
      requests.alerts=api('/api/alerts');
      requests.recommendations=api('/api/recommendations');
      requests.events=api('/api/supply-events');
      requests.districts=api('/api/districts');
      requests.audit=allowed.includes('governance')?api('/api/audit'):Promise.resolve([]);
    }
    if(allowed.includes('operations')) requests.medicines=api('/api/medicines');
    if(allowed.includes('planning')){
      requests.forecasts=api('/api/forecasts');
      requests.procurement=api('/api/procurement');
      if(!requests.recommendations) requests.recommendations=api('/api/recommendations');
    }
    if(allowed.includes('supply') && !requests.events) requests.events=api('/api/supply-events');
    if(allowed.includes('federated')){
      requests.fedNodes=api('/api/federated/nodes');
      requests.fedRound=api('/api/federated/round');
    }
    if(allowed.includes('governance')){
      requests.governance=api('/api/governance');
      requests.auditGov=api('/api/audit');
    }
    const entries=Object.entries(requests);
    const results=await Promise.all(entries.map(([,promise])=>promise));
    const data=Object.fromEntries(entries.map(([key],idx)=>[key,results[idx]]));
    cache.phcs=data.phcs||[];
    cache.medicines=data.medicines||cache.medicines||[];
    cache.conditions=data.conditions||cache.conditions||[];
    cache.recs=data.recommendations||cache.recs||[];
    fillPHCSelectors(cache.phcs);
    if(allowed.includes('overview')) renderOverview(data.dashboard,data.conditions||[],data.alerts||[],data.recommendations||[],data.events||[],data.districts||[],data.audit||[]);
    if(allowed.includes('operations')) renderInventory(data.medicines||[]);
    if(allowed.includes('planning')) renderPlanning(data.forecasts||[],data.procurement||[],data.recommendations||cache.recs||[]);
    if(allowed.includes('supply')) renderSupplyEvents(data.events||[]);
    if(allowed.includes('federated')) renderFederated(data.fedNodes||[],data.fedRound||{});
    if(allowed.includes('governance')) renderGovernance(data.governance||{},data.auditGov||[]);
  }catch(e){ console.error(e); notify(e.message,'error'); }
}


function fillPHCSelectors(phcs){
  const options=phcs.map(p=>`<option value="${p.id}">${esc(p.name)} — ${esc(p.district)}</option>`).join('');
  ['patientPhc','facilityPhc','dispatchPhc'].forEach(id=>{ const el=$(id); if(el) el.innerHTML=options; });
  if(currentUser?.role==='PHC' && currentUser.phc_id){ $('patientPhc').value=currentUser.phc_id; $('facilityPhc').value=currentUser.phc_id; }
}

function renderOverview(dash,conditions,alerts,recs,events,districts,audit){
  $('cards').innerHTML=[
    ['PHCs connected',dash.phcs,'Network nodes','neutral'],
    ['Patient records',dash.patient_records.toLocaleString(),'Aggregated encounters','neutral'],
    ['Bed utilisation',`${dash.bed_utilisation}%`,`${dash.high_bed_centres} centres ≥ 90%`,'warn'],
    ['Medicine units',dash.medicine_units.toLocaleString(),'Current known stock','neutral'],
    ['Procurement gap',dash.procurement_gap.toLocaleString(),'Units needing planning','danger']
  ].map(([l,v,s,k])=>`<div class="card"><div class="label">${l}</div><div class="value ${k}">${v}</div><div class="sub">${s}</div></div>`).join('');
  $('alerts').innerHTML=alerts.length?alerts.slice(0,7).map(a=>`<div class="alert"><div class="alert-row"><span class="pill ${a.severity.toLowerCase()}">${a.severity}</span><div><b>${esc(a.message)}</b><small>${esc(a.district)} · ${esc(a.phc)}</small></div></div></div>`).join(''):`<div class="empty">No priority alerts.</div>`;
  $('districtGrid').innerHTML=districts.map(d=>{
    const u=d.bed_utilisation; const state=u>=90?'critical':u>=75?'high':'ok';
    return `<div class="district"><div class="district-top"><strong>${esc(d.district)}</strong><span class="pill ${state}">${u}% beds</span></div><div class="district-meta"><span>${d.phcs} PHCs</span><span>${d.patient_records.toLocaleString()} records</span></div><div class="mini-bar"><i style="width:${Math.min(100,u)}%"></i></div><div class="district-foot"><span>${d.occupied}/${d.beds} beds</span><span>${d.critical_items} critical items</span></div></div>`;
  }).join('');
  $('recommendations').innerHTML=recs.length?recs.slice(0,5).map(recHTML).join(''):`<div class="empty">No feasible redistribution actions detected.</div>`;
  $('activityList').innerHTML=events.slice(0,6).map(e=>`<div class="activity"><div class="activity-icon">↗</div><div><b>${esc(e.medicine)}</b><span>${esc(e.source)} → ${esc(e.destination)}</span><small>${e.quantity} units · ${timeAgo(e.timestamp)}</small></div></div>`).join('') || '<div class="empty">No recent activity.</div>';
  $('auditTable').innerHTML=renderAudit(audit);
  drawChart(conditions);
}
function recHTML(r){
  const urgency=(r.to_days<=7)?'critical':(r.to_days<=14?'high':'medium');
  const canAct=['GOVERNMENT','DISTRICT'].includes(currentUser?.role);
  return `<div class="rec"><div class="rec-main"><div><div class="rec-title"><span class="pill ${urgency}">${urgency.toUpperCase()}</span><strong>${esc(r.medicine)}</strong></div><div class="rec-route">${esc(r.from_phc)} <span>→</span> ${esc(r.to_phc)}</div><small>${r.quantity} units · ~${r.distance_km} km · receiver has ${r.to_days} days remaining</small></div>${canAct?`<button class="small-btn" onclick='applyTransfer(${JSON.stringify(r).replaceAll("'","&#39;")})'>Apply transfer</button>`:''}</div></div>`;
}
function renderInventory(rows){
  const filter=$('inventoryFilter')?.value||'ALL'; const filtered=filter==='ALL'?rows:rows.filter(r=>r.status===filter);
  $('inventoryTable').innerHTML=filtered.map(r=>`<tr><td><strong>${esc(r.medicine)}</strong></td><td>${esc(r.phc)}<span class="table-sub">${esc(r.district)}</span></td><td><code>${esc(r.batch)}</code></td><td><strong>${r.quantity.toLocaleString()}</strong></td><td>${r.reorder_level.toLocaleString()}</td><td>${esc(r.expiry)}<span class="table-sub">${r.days_to_expiry}d</span></td><td><span class="pill ${statusClass(r.status)}">${esc(r.status)}</span></td></tr>`).join('') || '<tr><td colspan="7"><div class="empty">No inventory rows match this filter.</div></td></tr>';
}
function renderPlanning(forecasts,procurement,recs){
  $('forecastTable').innerHTML=forecasts.map(r=>`<tr><td><strong>${esc(r.medicine)}</strong><span class="table-sub">${esc(r.batch)}</span></td><td>${esc(r.phc)}</td><td>${r.stock.toLocaleString()}</td><td>${r.daily_use}</td><td class="${r.trend_pct>0?'trend-up':'trend-down'}">${r.trend_pct>0?'+':''}${r.trend_pct}%</td><td>${r.days_remaining}</td><td>${r.demand_30d.toLocaleString()}</td><td>${r.safety_stock.toLocaleString()}</td><td><span class="pill ${r.risk.toLowerCase()}">${r.risk}</span></td></tr>`).join('') || '<tr><td colspan="9"><div class="empty">No forecasts available.</div></td></tr>';
  $('procurementTable').innerHTML=procurement.map(r=>`<tr><td><strong>${esc(r.medicine)}</strong></td><td>${r.network_stock.toLocaleString()}</td><td>${r.demand_30d.toLocaleString()}</td><td>${r.safety_stock.toLocaleString()}</td><td><strong>${r.gap.toLocaleString()}</strong></td><td><span class="pill ${r.centres_at_risk?'high':'ok'}">${r.centres_at_risk}</span></td></tr>`).join('') || '<tr><td colspan="6"><div class="empty">No procurement gap detected.</div></td></tr>';
  $('planningRecommendations').innerHTML=recs.length?recs.map(recHTML).join(''):'<div class="empty">No feasible redistribution actions detected.</div>';
}
function renderSupplyEvents(events){
  $('supplyCards').innerHTML=events.map(e=>`<div class="event"><div class="event-top"><span class="pill ok">${esc(e.status)}</span><small>${timeAgo(e.timestamp)}</small></div><div class="route">${esc(e.source)} <span>→</span> ${esc(e.destination)}</div><div class="meta"><strong>${esc(e.medicine)}</strong> · ${e.quantity} units · batch <code>${esc(e.batch)}</code></div></div>`).join('') || '<div class="empty">No supply events yet.</div>';
}
function renderFederated(fed,round){
  $('fedRound').textContent=`Model round ${round.round}`;
  $('federatedTable').innerHTML=fed.nodes.map(n=>`<tr><td><strong>${esc(n.node)}</strong></td><td>${n.local_samples.toLocaleString()}</td><td>${Math.round(n.local_model_quality*100)}%</td><td><code>${esc(n.update_id)}</code></td><td><span class="pill ok">NO</span></td><td><span class="pill ok">${esc(n.status)}</span></td></tr>`).join('');
  $('globalModel').innerHTML=[['Global bias',round.global_bias.toFixed(4)],['Global slope',round.global_slope.toFixed(4)],['Participating nodes',round.participating_nodes],['Raw records exchanged',round.raw_data_shared?'YES':'NO']].map(x=>`<div><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');
}
function renderGovernance(g,audit){
  $('governanceGrid').innerHTML=[
    ['Raw patient data to suppliers','No','Aggregated demand only'],
    ['Raw data across federation','No','Parameter/update demo'],
    ['Audit events',g.audit_events,'Administrative activity'],
    ['Verification scans',g.verification_scans,'Batch checks'],
    ['Active roles',g.active_demo_roles.length,'RBAC demo']
  ].map(x=>`<div class="gov-card"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong><small>${esc(x[2])}</small></div>`).join('');
  $('auditTable').innerHTML=renderAudit(audit);
}
function renderAudit(audit){ return `<div class="table-wrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead><tbody>${audit.map(a=>`<tr><td>${timeAgo(a.timestamp)}</td><td>${esc(a.actor)}</td><td><span class="pill medium">${esc(a.action)}</span></td><td>${esc(a.target)}</td><td>${esc(a.detail)}</td></tr>`).join('')}</tbody></table></div>`; }
function statusClass(s){ return s==='STOCK-OUT'?'critical':s==='REORDER'?'high':s==='EXPIRING'?'medium':'ok'; }
function timeAgo(ts){ const d=new Date(ts); const mins=Math.max(0,Math.floor((Date.now()-d.getTime())/60000)); if(mins<1)return'just now'; if(mins<60)return`${mins}m ago`; const hrs=Math.floor(mins/60); if(hrs<24)return`${hrs}h ago`; return`${Math.floor(hrs/24)}d ago`; }

async function applyTransfer(r){
  if(!['GOVERNMENT','DISTRICT'].includes(currentUser.role)){notify('Only Government or District users can execute transfers','error');return;}
  if(!confirm(`Apply ${r.quantity} units of ${r.medicine} from ${r.from_phc} to ${r.to_phc}?`))return;
  try{ await api('/api/transfers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({medicine_name:r.medicine,batch_no:r.batch,from_phc_id:r.from_phc_id,to_phc_id:r.to_phc_id,quantity:r.quantity,note:'Applied from MedData recommendation'})}); notify('Transfer applied — inventory and chain of custody updated','success'); await loadAll(); }
  catch(e){notify(`Transfer failed: ${e.message}`,'error');}
}

function drawChart(rows){
  const c=$('conditionChart'); if(!c)return; const ctx=c.getContext('2d'); const dpr=window.devicePixelRatio||1; const w=c.clientWidth||700,h=260;
  c.width=w*dpr;c.height=h*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
  const max=Math.max(...rows.map(x=>x.cases),1),left=165,right=50,top=12,rowH=Math.max(32,Math.min(42,(h-20)/Math.max(rows.length,1))); const barMax=Math.max(80,w-left-right);
  rows.slice(0,6).forEach((r,i)=>{const y=top+i*rowH;ctx.fillStyle='#667085';ctx.font='12px system-ui';ctx.fillText(r.condition,4,y+16);ctx.fillStyle='#2867d6';ctx.fillRect(left,y+4,(r.cases/max)*barMax,18);ctx.fillStyle='#101828';ctx.font='600 12px system-ui';ctx.fillText(r.cases,left+(r.cases/max)*barMax+8,y+18);});
}

async function traceBatch(prefill){
  const batch=(prefill||$('batchInput').value).trim(); if(!batch){notify('Enter a batch number first','error');return;}
  $('batchInput').value=batch; $('traceResult').innerHTML='<div class="loading">Tracing batch…</div>';
  try{
    const r=await api('/api/trace/'+encodeURIComponent(batch));
    $('traceResult').innerHTML=`<div class="trace-result"><div class="trace-head"><div><div class="eyebrow">BATCH RECORD</div><strong>${esc(r.batch)}</strong></div><span class="pill ${r.verified?'ok':'critical'}">${r.verified?'TRACE OK':'REVIEW REQUIRED'}</span></div>${r.issues.length?`<div class="issue">${r.issues.map(esc).join('<br>')}</div>`:`<div class="good">No reconciliation issue detected in this demo trace.</div>`}<div class="chain"><div class="chain-title">Movement</div>${r.events.map(e=>`<div class="chain-step"><i></i><div><b>${esc(e.source)} → ${esc(e.destination)}</b><small>${e.quantity} units · ${esc(e.status)} · ${timeAgo(e.timestamp)}</small></div></div>`).join('')||'<div class="empty">No movement events.</div>'}</div><div class="chain"><div class="chain-title">Current known locations</div>${r.stock_locations.map(s=>`<div class="location-row"><span>${esc(s.phc)} · ${esc(s.district)}</span><b>${s.quantity} units</b><small>Expiry ${esc(s.expiry)}</small></div>`).join('')||'<div class="empty">No current stock location.</div>'}</div></div>`;
    $('qrPanel').innerHTML=`<div class="qr-box"><img class="qr" src="/api/qr/${encodeURIComponent(batch)}"><small>Use the QR verifier on the same network.</small></div><div><div class="eyebrow">VERIFICATION</div><h3>Record a chain check</h3><p class="muted">The result is written to the administrative audit trail.</p><div class="button-row"><button class="primary" onclick="recordVerification('${esc(batch)}','VERIFIED')">Mark verified</button><button class="secondary" onclick="recordVerification('${esc(batch)}','REVIEW')">Flag for review</button></div></div>`;
  }catch(e){$('traceResult').innerHTML=`<div class="empty">Batch not found in the demo network.</div>`;notify(e.message,'error');}
}
async function recordVerification(batch,result){ try{await api('/api/trace/'+encodeURIComponent(batch)+'/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({result,note:'Manual/QR demo verification'})});notify(`Batch ${batch} marked ${result.toLowerCase()}`,'success');}catch(e){notify(`Verification failed: ${e.message}`,'error');} }

async function runSimulation(){
  const condition=$('simCondition').value, multiplier=+$('simMultiplier').value; $('simResult').innerHTML='<div class="loading">Running scenario across the network…</div>';
  try{
    const r=await api(`/api/emergency/simulate?condition=${encodeURIComponent(condition)}&multiplier=${multiplier}`); const high=r.phc_impact.filter(x=>x.capacity_risk==='HIGH').length;
    const stress=Object.entries(r.medicine_impact).map(([m,x])=>`${esc(m)} ${Math.round(x.stress_ratio*100)}%`).join(' · ');
    const recs=r.surge_redistribution||[];
    $('simResult').innerHTML=`<div class="sim-summary"><div><span>Scenario</span><strong>${esc(r.condition)} × ${r.multiplier}</strong></div><div><span>High capacity risk</span><strong>${high} centres</strong></div><div><span>Medicine stress</span><strong>${stress||'—'}</strong></div></div><div class="table-wrap"><table><thead><tr><th>PHC</th><th>District</th><th>Current</th><th>Projected</th><th>Extra</th><th>Beds needed</th><th>Free beds</th><th>Risk</th></tr></thead><tbody>${r.phc_impact.map(x=>`<tr><td>${esc(x.phc)}</td><td>${esc(x.district)}</td><td>${x.current_cases}</td><td>${x.projected_cases}</td><td>${x.incremental_cases}</td><td>${x.estimated_bed_need}</td><td>${x.free_beds}</td><td><span class="pill ${x.capacity_risk.toLowerCase()}">${x.capacity_risk}</span></td></tr>`).join('')}</tbody></table></div><div class="panel inset"><div class="panel-head"><div><h2>Suggested surge redistribution</h2><span>Emergency-scaled demand</span></div><button class="ghost" onclick="setPage('planning'); activatePlanningTab('redistributionPanel')">Open actions →</button></div>${recs.length?recs.slice(0,6).map(recHTML).join(''):'<div class="empty">No feasible internal transfer found.</div>'}</div><div class="demo-note">${esc(r.warning)}</div>`;
  }catch(e){$('simResult').innerHTML=`<div class="empty">Simulation failed: ${esc(e.message)}</div>`;}
}
async function runFedRound(){
  if(!['GOVERNMENT','DISTRICT'].includes(currentUser.role)){notify('Only Government or District users can start a federation round','error');return;}
  try{const r=await api('/api/federated/run',{method:'POST'});$('fedResult').textContent=`Round ${r.round} completed: weighted parameter averaging across ${r.participating_nodes} nodes; raw data shared = ${r.raw_data_shared}.`;renderFederated(await api('/api/federated/nodes'),await api('/api/federated/round'));notify('Federated model round completed','success');}catch(e){$('fedResult').textContent='Federation round failed: '+e.message;}
}
function activatePlanningTab(panelId){
  document.querySelectorAll('.mini-tab').forEach(b=>b.classList.remove('active')); document.querySelector(`.mini-tab[data-panel="${panelId}"]`)?.classList.add('active');
  document.querySelectorAll('.mini-panel').forEach(p=>p.classList.remove('active')); $(panelId).classList.add('active');
}
async function downloadCSV(kind){
  try{
    const r=await fetch(`/api/export/${kind}.csv`,{credentials:'same-origin'}); if(!r.ok) throw new Error('Export failed');
    const blob=await r.blob(); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=`MedData_${kind}.csv`; a.click(); URL.revokeObjectURL(url); notify('CSV export downloaded','success');
  }catch(e){notify(e.message,'error');}
}

$('patientForm')?.addEventListener('submit',async e=>{e.preventDefault(); try{await api('/api/patients',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phc_id:+$('patientPhc').value,condition:$('condition').value.trim(),age:+$('age').value,sex:$('sex').value})});$('patientMessage').textContent='Encounter saved. Aggregated dashboards will reflect it on refresh.';e.target.reset();if(currentUser.role==='PHC')$('patientPhc').value=currentUser.phc_id;notify('Patient encounter recorded','success');await loadAll();}catch(err){$('patientMessage').textContent='Could not save encounter: '+err.message;notify(err.message,'error');}});
$('facilityForm')?.addEventListener('submit',async e=>{e.preventDefault();const body={};if($('beds').value!=='')body.beds=+$('beds').value;if($('occupied').value!=='')body.occupied_beds=+$('occupied').value;if($('staff').value!=='')body.staff_present=+$('staff').value;try{await api(`/api/phcs/${$('facilityPhc').value}/facility`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('facilityMessage').textContent='Facility status updated and audit logged.';notify('Facility status updated','success');await loadAll();}catch(err){$('facilityMessage').textContent='Update failed: '+err.message;notify(err.message,'error');}});
$('dispatchForm')?.addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/supply/dispatch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({medicine_name:$('dispatchMedicine').value.trim(),batch_no:$('dispatchBatch').value.trim(),quantity:+$('dispatchQty').value,expiry_date:$('dispatchExpiry').value.trim(),reorder_level:+$('dispatchReorder').value,destination_phc_id:+$('dispatchPhc').value})});$('dispatchMessage').textContent='Supplier dispatch recorded. Inventory and chain of custody updated.';notify('Supplier dispatch recorded','success');await loadAll();traceBatch($('dispatchBatch').value.trim());}catch(err){$('dispatchMessage').textContent='Dispatch failed: '+err.message;notify(err.message,'error');}});
$('inventoryFilter')?.addEventListener('change',()=>renderInventory(cache.medicines));
// Main navigation: the dashboard is a single-page app, so sidebar buttons
// must switch sections without attempting a full-page navigation.
document.querySelectorAll('.nav[data-section]').forEach(button=>{
  button.addEventListener('click', ()=>{
    const section = button.dataset.section;
    if(section) setPage(section);
  });
});

// Allow dashboard sections to be opened directly with #overview, #operations, etc.
// while preserving role-based access.
function applyHashNavigation(){
  const section = (location.hash || '').slice(1);
  if(section && pageMeta[section] && roleAccess[currentUser?.role || 'GOVERNMENT']?.includes(section)){
    setPage(section);
  }
}
window.addEventListener('hashchange', applyHashNavigation);

document.querySelectorAll('.mini-tab').forEach(b=>b.addEventListener('click',()=>activatePlanningTab(b.dataset.panel)));
$('globalSearch')?.addEventListener('input',async e=>{
  const q=e.target.value.trim(); const box=$('searchResults'); if(q.length<2){box.hidden=true;return;}
  try{const rows=await api('/api/search?q='+encodeURIComponent(q));box.innerHTML=rows.length?rows.map(x=>`<button class="search-item" onclick="searchPick(${JSON.stringify(x)})"><b>${esc(x.label)}</b><small>${esc(x.type)} · ${esc(x.meta)}</small></button>`).join(''):'<div class="search-empty">No matching PHC, medicine or batch.</div>';box.hidden=false;}catch(e){box.hidden=true;}
});
function searchPick(x){ $('globalSearch').value=x.label; $('searchResults').hidden=true; if(x.type==='BATCH'){setPage('supply');traceBatch(x.key);} else if(x.type==='MEDICINE'){setPage('operations');$('inventoryFilter').value='ALL';document.getElementById('inventoryTable').scrollIntoView({behavior:'smooth',block:'center'});} else {setPage('overview');notify(`${x.label} · ${x.meta}`,'info');} }

document.addEventListener('click',e=>{if(!e.target.closest('.search'))$('searchResults').hidden=true;});
window.addEventListener('resize',()=>{if(currentUser?.role==='GOVERNMENT'||currentUser?.role==='DISTRICT') drawChart(cache.conditions||[]);});
bootstrap();
