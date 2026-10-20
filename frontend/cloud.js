/* Cognito authorization code + PKCE. Tokens stay in memory, never in storage. */
let bearer = null;
let refreshToken = null;
let expiresAt = 0;
let currentSubject = null;
let lastRun = null;
let pollTimer = null;
const $ = id => document.getElementById(id);
const statusText = $('status');
const result = $('result');
const signin = $('signin');
const signout = $('signout');
const labSignin = $('lab-signin');
const seed = $('seed');
const recover = $('recover');
const download = $('download');
function b64(bytes) { return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
function setStatus(text, kind = 'idle') {
  statusText.textContent = text;
  $('status-marker').className = 'status-marker' + (kind === 'busy' ? ' busy' : kind === 'error' ? ' error' : '');
}
function showRaw(value) { result.textContent = JSON.stringify(value, null, 2); }
function counts(value) {
  if (!value || !['profiles','documents','index_chunks'].every(key => Number.isInteger(value[key]) && value[key] >= 0)) return '— / — / —';
  return [value.profiles,value.documents,value.index_chunks].join(' / ');
}
function resetEvidence() {
  lastRun = null;
  download.disabled = true;
  $('before-count').textContent = '— / — / —';
  $('after-count').textContent = '— / — / —';
  $('release-state').textContent = 'NOT RUN';
  $('signature-state').textContent = 'No signing result yet. Run an experiment to collect evidence.';
  $('evidence-description').textContent = 'A clean result starts with an honest empty page.';
  $('evidence-note').textContent = '↳ Your run’s evidence will appear here.';
  document.querySelector('.pipeline').classList.remove('is-complete');
  $('step-recover').classList.remove('is-complete');
}
async function api(path, payload) {
  if (refreshToken && Date.now() > expiresAt - 60000) {
    const renewal = await fetch(AUTH_CONFIG.authDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({grant_type:'refresh_token',client_id:AUTH_CONFIG.clientId,refresh_token:refreshToken})});
    const tokens = await renewal.json();
    if (!renewal.ok || !tokens.id_token) throw new Error('Session expired. Sign in again.');
    bearer = tokens.id_token;
    expiresAt = Date.now() + tokens.expires_in*1000;
  }
  const response = await fetch(path,{method:payload?'POST':'GET',headers:{Authorization:'Bearer '+bearer,'Content-Type':'application/json'},body:payload?JSON.stringify(payload):undefined});
  const value = await response.json();
  if (!response.ok) throw new Error(response.status === 503 ? 'The lab is unavailable. If the database is paused, resume it before trying again.' : value.detail || 'Request failed ('+response.status+')');
  return value;
}
async function signIn() {
  const verifier = b64(crypto.getRandomValues(new Uint8Array(48)));
  const state = b64(crypto.getRandomValues(new Uint8Array(24)));
  sessionStorage.setItem('rgx-pkce',JSON.stringify({verifier,state}));
  const challenge = b64(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
  const url = new URL(AUTH_CONFIG.authDomain+'/oauth2/authorize');
  url.search = new URLSearchParams({client_id:AUTH_CONFIG.clientId,response_type:'code',scope:'openid profile',redirect_uri:AUTH_CONFIG.redirectUri,state,code_challenge:challenge,code_challenge_method:'S256'});
  location.assign(url);
}
function startSignIn() { signIn().catch(error => setStatus(error.message, 'error')); }
signin.onclick = startSignIn;
labSignin.onclick = startSignIn;
async function callback() {
  const params = new URLSearchParams(location.search);
  if (params.has('error')) {
    sessionStorage.removeItem('rgx-pkce');
    history.replaceState(null,'','/ui');
    throw new Error('Sign-in was not completed. You can try again.');
  }
  if (!params.has('code')) return;
  const stored = JSON.parse(sessionStorage.getItem('rgx-pkce') || 'null');
  sessionStorage.removeItem('rgx-pkce');
  if (!stored || params.get('state') !== stored.state) {
    history.replaceState(null,'','/ui');
    throw new Error('Sign-in state mismatch. Start sign-in again.');
  }
  const response = await fetch(AUTH_CONFIG.authDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({grant_type:'authorization_code',client_id:AUTH_CONFIG.clientId,redirect_uri:AUTH_CONFIG.redirectUri,code:params.get('code'),code_verifier:stored.verifier})});
  const tokens = await response.json();
  history.replaceState(null,'','/ui');
  if (!response.ok || !tokens.id_token) throw new Error('Sign-in could not be completed.');
  bearer = tokens.id_token;
  refreshToken = tokens.refresh_token;
  expiresAt = Date.now() + tokens.expires_in*1000;
  const me = await api('/v1/me');
  $('identity').textContent = me.operator ? 'You’re signed in as a lab operator. Let’s make a little controlled chaos.' : 'You’re signed in. A lab operator role is required to create experiments.';
  signin.hidden = true;
  signout.hidden = false;
  labSignin.textContent = 'You’re signed in ✓';
  labSignin.disabled = true;
  $('session-label').hidden = false;
  $('session-label').textContent = me.operator ? 'LAB OPERATOR ↗' : 'SIGNED IN ↗';
  $('step-auth').classList.add('is-complete');
  seed.disabled = !me.operator;
  $('workflow-state').textContent = me.operator ? 'LET’S MAKE SOME DATA' : 'SIGNED IN';
  setStatus(me.operator ? 'Your notebook is ready. Create a synthetic profile to begin.' : 'Ask the project owner for lab operator access to run an experiment.');
}
seed.onclick = async () => {
  seed.disabled = true;
  recover.disabled = true;
  resetEvidence();
  $('step-seed').classList.remove('is-complete');
  try {
    currentSubject = 'demo_'+crypto.randomUUID().replaceAll('-','');
    $('workflow-state').textContent = 'MAKING TEST DATA';
    setStatus('Creating a synthetic profile, document, and retrieval index.', 'busy');
    const value = await api('/v1/lab/seed',{subject_id:currentSubject,request_id:'seed_'+crypto.randomUUID().replaceAll('-','')});
    showRaw(value);
    $('step-seed').classList.add('is-complete');
    $('workflow-state').textContent = 'READY TO REWIND';
    $('evidence-description').textContent = 'Your synthetic profile is ready. Run recovery to collect the proof.';
    setStatus('Test data is ready. Next: run guarded recovery. Keep this page open while it runs.');
    recover.disabled = false;
  } catch(error) {
    currentSubject = null;
    $('workflow-state').textContent = 'LET’S TRY THAT AGAIN';
    setStatus(error.message, 'error');
    seed.disabled = false;
  }
};
function renderRun(value) {
  showRaw(value);
  const output = value.result || {};
  $('before-count').textContent = counts(output.raw_before);
  $('after-count').textContent = counts(output.raw_after);
  if (value.status === 'RUNNING') {
    $('release-state').textContent = 'CHECKING';
    $('signature-state').textContent = 'Evidence is being collected. Release has not been confirmed.';
    return;
  }
  lastRun = value;
  download.disabled = false;
  const released = value.status === 'SUCCEEDED' && output.release && output.release.state === 'RELEASED';
  $('release-state').textContent = released ? 'RELEASED' : 'HELD';
  $('signature-state').textContent = output.evidence_signed ? 'Evidence signed by AWS KMS. The run JSON records the signing result.' : 'No signing result was returned for this run.';
  $('workflow-state').textContent = released ? 'THE PAST STAYS PUT' : 'RECOVERY HELD';
  $('evidence-description').textContent = released ? 'Old data met a new boundary. Here’s what your run actually found.' : 'The gate stayed closed. Review the run details before trying again.';
  $('evidence-note').textContent = released ? '↳ The trusted recovery workflow approved this candidate before publication.' : (value.detail || '↳ A held workflow is not evidence of successful recovery. Check project monitoring.');
  if (released) {
    $('step-recover').classList.add('is-complete');
    document.querySelector('.pipeline').classList.add('is-complete');
  }
  setStatus(released ? 'Guarded recovery completed. Your verification results are ready below.' : (value.detail || 'Recovery held. Review project monitoring before retrying.'), released ? 'idle' : 'error');
  seed.disabled = false;
}
async function poll(runId) {
  try {
    const value = await api('/v1/runs/'+runId);
    renderRun(value);
    if (value.status === 'RUNNING') pollTimer = setTimeout(() => poll(runId), 15000);
  } catch(error) {
    $('workflow-state').textContent = 'STATUS CHECK PAUSED';
    $('release-state').textContent = 'UNKNOWN';
    setStatus(error.message+' The workflow may still be running; inspect project monitoring before starting another.', 'error');
  }
}
recover.onclick = async () => {
  if (!currentSubject) return;
  recover.disabled = true;
  seed.disabled = true;
  if (pollTimer) clearTimeout(pollTimer);
  try {
    $('workflow-state').textContent = 'REWIND IN PROGRESS';
    setStatus('Starting snapshot → erasure → isolated restore → verification → release.', 'busy');
    const run = await api('/v1/lab/recovery',{subject_id:currentSubject});
    showRaw(run);
    $('evidence-description').textContent = 'Collecting real evidence. Recovery can take 15–40 minutes; keep this page open.';
    $('release-state').textContent = 'CHECKING';
    pollTimer = setTimeout(() => poll(run.run_id), 15000);
  } catch(error) {
    $('workflow-state').textContent = 'START NOT CONFIRMED';
    setStatus(error.message+' Check project monitoring before starting another run.', 'error');
  }
};
download.onclick = () => {
  if (!lastRun) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(lastRun,null,2)],{type:'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'rewindguard-run.json';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
signout.onclick = () => {
  if (pollTimer) clearTimeout(pollTimer);
  bearer = null;
  refreshToken = null;
  expiresAt = 0;
  currentSubject = null;
  sessionStorage.removeItem('rgx-pkce');
  resetEvidence();
  seed.disabled = true;
  recover.disabled = true;
  setStatus('Signing out. Submitted workflows continue in the project.');
  const url = new URL(AUTH_CONFIG.authDomain+'/logout');
  url.search = new URLSearchParams({client_id:AUTH_CONFIG.clientId,logout_uri:new URL('/ui',AUTH_CONFIG.redirectUri).href});
  location.assign(url);
};
callback().catch(error => setStatus(error.message, 'error'));
