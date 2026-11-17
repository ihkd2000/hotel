/* Innkeeper: small vanilla-JS browser client. Nothing is stored in localStorage. */
const app = document.querySelector('#app');
let actor = null;
let tab = 'dashboard';
let rooms = [], guests = [], reservations = [], stats = {};
let popup = null;
let notice = '';

const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dollars = cents => '$' + (Number(cents || 0) / 100).toFixed(2);
const badge = value => `<span class="pill ${esc(value)}">${esc(value.replaceAll('_',' '))}</span>`;
const currentDate = () => new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
const plusDays = (date, days) => {const d = new Date(date+'T12:00:00');d.setDate(d.getDate()+days);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`};
async function api(path, data) {
  const options = {credentials:'same-origin'};
  if (data !== undefined) {options.method='POST'; options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(data)}
  const r = await fetch('/api'+path, options);
  const output = await r.json();
  if (!r.ok) {if(r.status===401 && path!='/login') { actor = null; renderLogin() } throw Error(output.error || 'Request failed')}
  return output;
}
const field=(name,label,type='text',value='',extra='')=>`<div class="field"><label for="${name}">${esc(label)}</label><input id="${name}" name="${name}" type="${type}" value="${esc(value)}" ${extra}></div>`;
const selectField=(name,label,items)=>`<div class="field"><label for="${name}">${esc(label)}</label><select id="${name}" name="${name}">${items.map(x=>`<option value="${esc(x.value)}">${esc(x.text)}</option>`).join('')}</select></div>`;
function showMessage(msg, isError=false) { notice = `<div class="alert ${isError?'':'success'}">${esc(msg)}</div>`; renderPage() }
function renderLogin(message='') {
  popup=null;
  app.innerHTML=`<div class="login-wrap"><form class="login" id="login"><div class="kicker">Front desk workspace</div><h1>Innkeeper</h1><p class="muted">Sign in to manage your property.</p>${message?`<div class="alert">${esc(message)}</div>`:''}${field('username','Username')}${field('password','Password','password')}<button class="btn" type="submit">Sign in</button><p class="muted">First run? Create an administrator with <code>python -m hotel --init-admin</code>.</p></form></div>`;
  document.querySelector('#login').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.target);try {await api('/login',{username:form.get('username'),password:form.get('password')});actor=await api('/me');await refresh();}catch(err){renderLogin(err.message)}};
}
async function refresh(){
  try {[rooms, guests, reservations, stats]=await Promise.all([api('/rooms'),api('/guests'),api('/reservations'),api('/dashboard')]);renderPage();}
  catch(err){if(actor)showMessage(err.message,true)}
}
function renderPage(){
  if(!actor)return renderLogin();
  const links=[['dashboard','Overview'],['rooms','Rooms'],['guests','Guests'],['reservations','Reservations']];
  if(actor.role==='admin')links.push(['staff','Staff'],['activity','Activity']);
  const names={dashboard:'Overview',rooms:'Room inventory',guests:'Guest directory',reservations:'Reservations',staff:'Staff accounts',activity:'Activity log'};
  app.innerHTML=`<div class="layout"><aside class="side"><div><div class="brand">Innkeeper</div><div class="brand-sub">HOTEL OPERATIONS</div></div>${links.map(([id,label])=>`<button class="nav ${tab===id?'selected':''}" data-tab="${id}">${label}</button>`).join('')}<div class="sidebar-foot">${esc(actor.username)} · ${esc(actor.role)}<div style="margin-top:10px"><button class="btn light small" id="signout">Sign out</button></div></div></aside><main class="content"><header class="top"><div><div class="kicker">Property workspace</div><h1>${names[tab]}</h1><p class="muted">Reservations, stays and front desk records in one place</p></div><span class="muted">${esc(currentDate())}</span></header><div id="flash">${notice}</div><div id="section"></div></main></div>`;
  notice='';
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;renderPage();if(tab==='activity'||tab==='staff')loadAdmin()});
  document.querySelector('#signout').onclick=async()=>{try {await api('/logout',{})}finally {actor=null;renderLogin()}};
  if(tab==='dashboard')dashboardView();
  if(tab==='rooms')roomView();
  if(tab==='guests')guestView();
  if(tab==='reservations')reservationView();
  if(tab==='staff'||tab==='activity')loadAdmin();
}
function dashboardView(){
  document.querySelector('#section').innerHTML=`<div class="cards">${[['Total rooms',stats.rooms],['Out of service',stats.out_of_service],['Expected arrivals',stats.booked],['Checked-in stays',stats.checked_in],['Guest profiles',stats.guests]].map(([label,count])=>`<div class="metric"><span>${label}</span><strong>${count}</strong></div>`).join('')}</div><div class="panel"><div class="top"><h2>Recent reservations</h2><button class="btn" id="new-booking">New reservation</button></div>${reservationTable(reservations.slice(0,6))}</div>`;
  document.querySelector('#new-booking').onclick=()=>bookingDialog();wireBookingActions();
}
function roomView(){
  document.querySelector('#section').innerHTML=`<div class="panel"><div class="top"><h2>Room inventory</h2>${actor.role==='admin'?'<button class="btn" id="new-room">Add room</button>':''}</div><div class="table-wrap"><table><thead><tr><th>Number</th><th>Type</th><th>Nightly rate</th><th>State</th><th>Actions</th></tr></thead><tbody>${rooms.map(r=>`<tr><td><strong>${esc(r.number)}</strong></td><td>${esc(r.room_type)}</td><td>${dollars(r.nightly_rate_cents)}</td><td>${badge(r.state)}</td><td>${actor.role==='admin'?`<button class="btn light small" data-room="${r.id}" data-state="${r.state==='ready'?'out_of_service':'ready'}">${r.state==='ready'?'Take out of service':'Make ready'}</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>${rooms.length?'':'<div class="empty">Add your first room to start taking reservations.</div>'}</div>`;
  document.querySelector('#new-room')?.addEventListener('click',()=>dialog('Add a room',`${field('number','Room number','text','','required')}${field('room_type','Room type','text','','required')}${field('rate','Nightly rate (USD)','number','120.00','step="0.01" min="0" required')}`,async f=>{let value=Number(f.get('rate'));if(!Number.isFinite(value))throw Error('Invalid rate');await api('/rooms',{number:f.get('number'),room_type:f.get('room_type'),nightly_rate_cents:Math.round(value*100)})}));
  document.querySelectorAll('[data-room]').forEach(b=>b.onclick=async()=>{if(!confirm(`Change room status to ${b.dataset.state.replaceAll('_',' ')}?`))return;try{await api(`/rooms/${b.dataset.room}/state`,{state:b.dataset.state});await refresh()}catch(err){showMessage(err.message,true)}});
}
function guestView(){
  document.querySelector('#section').innerHTML=`<div class="panel"><div class="top"><h2>Guest profiles</h2><button class="btn" id="new-guest">Add guest</button></div><input class="filter" id="find-guest" placeholder="Search guests…"><div id="guest-list"></div></div>`;
  const list=()=>{const q=document.querySelector('#find-guest').value.toLowerCase();document.querySelector('#guest-list').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Phone</th></tr></thead><tbody>${guests.filter(g=>(g.full_name+' '+g.email+' '+g.phone).toLowerCase().includes(q)).map(g=>`<tr><td><strong>${esc(g.full_name)}</strong></td><td>${esc(g.email||'—')}</td><td>${esc(g.phone||'—')}</td></tr>`).join('')}</tbody></table></div>`};
  document.querySelector('#find-guest').oninput=list;list();
  document.querySelector('#new-guest').onclick=()=>dialog('New guest',`${field('full_name','Full name','text','','required')}${field('email','Email','email')}${field('phone','Phone')}`,async f=>api('/guests',{full_name:f.get('full_name'),email:f.get('email'),phone:f.get('phone')}));
}
function reservationTable(list){return `<div class="table-wrap"><table><thead><tr><th>ID</th><th>Guest</th><th>Room</th><th>Stay</th><th>Rate</th><th>Status</th><th>Actions</th></tr></thead><tbody>${list.map(b=>`<tr><td>#${b.id}</td><td><strong>${esc(b.guest_name)}</strong></td><td>${esc(b.room_number)}</td><td>${esc(b.check_in)} → ${esc(b.check_out)}<div class="muted">${b.nights} night${b.nights===1?'':'s'}</div></td><td>${dollars(b.rate_cents)} / night</td><td>${badge(b.status)}</td><td>${b.status==='booked'?`<button class="btn small" data-book="${b.id}" data-action="check_in">Check in</button><button class="btn light small" data-book="${b.id}" data-action="cancel">Cancel</button>`:b.status==='checked_in'?`<button class="btn small" data-book="${b.id}" data-action="check_out">Check out</button>`:'—'}</td></tr>`).join('')}</tbody></table></div>${list.length?'':'<div class="empty">No reservations to display.</div>'}`}
function reservationView(){
  document.querySelector('#section').innerHTML=`<div class="panel"><div class="top"><h2>Stay bookings</h2><button class="btn" id="new-booking">New reservation</button></div><div class="filters"><select class="filter" id="status-filter"><option value="">All statuses</option>${['booked','checked_in','checked_out','cancelled'].map(s=>`<option value="${s}">${s.replaceAll('_',' ')}</option>`).join('')}</select><input class="filter" id="booking-filter" placeholder="Search guest or room"></div><div id="booking-list"></div></div>`;
  const list=()=>{const st=document.querySelector('#status-filter').value;const q=document.querySelector('#booking-filter').value.toLowerCase();document.querySelector('#booking-list').innerHTML=reservationTable(reservations.filter(b=>(!st||b.status===st)&&(b.guest_name+' '+b.room_number).toLowerCase().includes(q)));wireBookingActions()};
  document.querySelector('#status-filter').onchange=list;document.querySelector('#booking-filter').oninput=list;document.querySelector('#new-booking').onclick=()=>bookingDialog();list();
}
function wireBookingActions(){document.querySelectorAll('[data-book]').forEach(b=>b.onclick=async()=>{if(!confirm(`Confirm ${b.dataset.action.replaceAll('_',' ')} for reservation #${b.dataset.book}?`))return;try{await api(`/reservations/${b.dataset.book}/${b.dataset.action}`,{});await refresh()}catch(err){showMessage(err.message,true)}})}
function bookingDialog(){
  if(!rooms.length||!guests.length)return alert('Add at least one room and one guest before creating a reservation.');
  const start=currentDate(), end=plusDays(start,1);
  dialog('New reservation',`${selectField('guest_id','Guest',guests.map(g=>({value:g.id,text:g.full_name})))}<div class="form-grid">${field('check_in','Arrival','date',start,'required')}${field('check_out','Departure','date',end,'required')}</div><div class="field"><label>Available room</label><select id="room_id" name="room_id"></select></div><div class="field"><label>Note (optional)</label><textarea name="note" rows="2"></textarea></div>`,async f=>{const id=Number(f.get('room_id'));if(!id)throw Error('Choose an available room');await api('/reservations',{room_id:id,guest_id:Number(f.get('guest_id')),check_in:f.get('check_in'),check_out:f.get('check_out'),note:f.get('note')})});
  const fill=async()=>{const d=document.querySelector('#check_in')?.value,e=document.querySelector('#check_out')?.value,select=document.querySelector('#room_id');if(!select)return;try{const available=await api(`/available?check_in=${encodeURIComponent(d)}&check_out=${encodeURIComponent(e)}`);select.innerHTML=available.length?available.map(r=>`<option value="${r.id}">${esc(r.number)} · ${esc(r.room_type)} · ${dollars(r.nightly_rate_cents)}</option>`).join(''):'<option value="">No available rooms</option>'}catch(err){select.innerHTML='<option value="">Invalid date range</option>'}};
  document.querySelector('#check_in').onchange=fill;document.querySelector('#check_out').onchange=fill;fill();
}
function dialog(title,content,save){
  const backdrop=document.createElement('div');backdrop.className='overlay';
  backdrop.innerHTML=`<form class="dialog" id="modal"><h2>${esc(title)}</h2><p>Complete the fields below and save your changes.</p><div id="modal-error"></div>${content}<div class="form-actions"><button class="btn light" type="button" id="close">Cancel</button><button class="btn" type="submit">Save</button></div></form>`;
  document.body.append(backdrop);
  const close=()=>backdrop.remove();
  backdrop.querySelector('#close').onclick=close;
  backdrop.onclick=e=>{if(e.target===backdrop)close()};
  backdrop.querySelector('form').onsubmit=async e=>{e.preventDefault();const submit=e.target.querySelector('[type=submit]');submit.disabled=true;try{await save(new FormData(e.target));close();await refresh()}catch(err){backdrop.querySelector('#modal-error').innerHTML=`<div class="alert">${esc(err.message)}</div>`}finally{submit.disabled=false}};
}
async function loadAdmin(){
  try{
    if(tab==='staff'){
      const users=await api('/users');
      document.querySelector('#section').innerHTML=`<div class="panel"><div class="top"><h2>Staff members</h2><button class="btn" id="new-user">Add staff member</button></div><div class="table-wrap"><table><thead><tr><th>Username</th><th>Role</th><th>Active</th></tr></thead><tbody>${users.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.role)}</td><td>${u.active?'Yes':'No'}</td></tr>`).join('')}</tbody></table></div></div>`;
      document.querySelector('#new-user').onclick=()=>dialog('Add staff',`${field('username','Username','text','','required')}${field('password','Password (10+ characters)','password','','minlength="10" required')}${selectField('role','Role',[{value:'frontdesk',text:'Front desk'},{value:'admin',text:'Administrator'}])}`,async f=>api('/users',{username:f.get('username'),password:f.get('password'),role:f.get('role')}));
    }else{
      const activity=await api('/activity');
      document.querySelector('#section').innerHTML=`<div class="panel"><h2>Recent activity</h2><div class="table-wrap"><table><thead><tr><th>When (UTC)</th><th>Actor</th><th>Action</th><th>Details</th></tr></thead><tbody>${activity.map(a=>`<tr><td>${esc(a.occurred_at)}</td><td>${esc(a.actor)}</td><td>${esc(a.action.replaceAll('_',' '))}</td><td>${esc(a.details)}</td></tr>`).join('')}</tbody></table></div></div>`;
    }
  }catch(err){showMessage(err.message,true)}
}
(async()=>{try{actor=await api('/me');await refresh()}catch{renderLogin()}})();
