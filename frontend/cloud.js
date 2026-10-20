/* Cognito authorization code flow with PKCE. Tokens stay in memory. */
let bearer = null;
let refreshToken = null;
let expiresAt = 0;
let currentSubject = null;
const statusText = document.getElementById('status');
const result = document.getElementById('result');
const signin = document.getElementById('signin');
const seed = document.getElementById('seed');
const recover = document.getElementById('recover');
function b64(bytes) { return btoa(String.fromCharCode(...bytes)).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
async function api(path, payload) {
  if (refreshToken && Date.now() > expiresAt - 60000) {
    const renewal = await fetch(AUTH_CONFIG.authDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({grant_type:'refresh_token',client_id:AUTH_CONFIG.clientId,refresh_token:refreshToken})});
    const tokens = await renewal.json();
    if (!renewal.ok || !tokens.id_token) throw new Error('Session expired. Sign in again.');
    bearer = tokens.id_token;
    expiresAt = Date.now() + tokens.expires_in*1000;
  }
  const response = await fetch(path,{method:payload?'POST':'GET',headers:{Authorization:`Bearer ${bearer}`,'Content-Type':'application/json'},body:payload?JSON.stringify(payload):undefined});
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
  return value;
}
signin.onclick = async () => {
  const verifier = b64(crypto.getRandomValues(new Uint8Array(48)));
  const state = b64(crypto.getRandomValues(new Uint8Array(24)));
  sessionStorage.setItem('rgx-pkce',JSON.stringify({verifier,state}));
  const challenge = b64(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier))));
  const url = new URL(AUTH_CONFIG.authDomain+'/oauth2/authorize');
  url.search = new URLSearchParams({client_id:AUTH_CONFIG.clientId,response_type:'code',scope:'openid profile',redirect_uri:AUTH_CONFIG.redirectUri,state,code_challenge:challenge,code_challenge_method:'S256'});
  location.assign(url);
};
async function callback() {
  const params = new URLSearchParams(location.search);
  if (!params.has('code')) return;
  const stored = JSON.parse(sessionStorage.getItem('rgx-pkce') || 'null');
  sessionStorage.removeItem('rgx-pkce');
  if (!stored || params.get('state') !== stored.state) throw new Error('Sign-in state mismatch. Start sign-in again.');
  const response = await fetch(AUTH_CONFIG.authDomain+'/oauth2/token',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({grant_type:'authorization_code',client_id:AUTH_CONFIG.clientId,redirect_uri:AUTH_CONFIG.redirectUri,code:params.get('code'),code_verifier:stored.verifier})});
  const tokens = await response.json();
  if (!response.ok || !tokens.id_token) throw new Error('Sign-in could not be completed.');
  bearer = tokens.id_token;
  refreshToken = tokens.refresh_token;
  expiresAt = Date.now() + tokens.expires_in*1000;
  history.replaceState(null,'','/ui');
  const me = await api('/v1/me');
  document.getElementById('identity').textContent = me.operator ? 'Signed in as a project lab operator.' : 'Signed in to your project.';
  signin.hidden = true;
  seed.disabled = !me.operator;
}
seed.onclick = async () => {
  seed.disabled = true;
  try {
    currentSubject = 'demo_'+crypto.randomUUID().replaceAll('-','');
    statusText.textContent = 'Creating a synthetic profile and retrieval index.';
    const value = await api('/v1/lab/seed',{subject_id:currentSubject,request_id:'seed_'+crypto.randomUUID().replaceAll('-','')});
    result.textContent = JSON.stringify(value,null,2);
    statusText.textContent = 'Synthetic data is ready. The restore experiment can take 15–40 minutes.';
    recover.disabled = false;
  } catch(error) { statusText.textContent = error.message; seed.disabled = false; }
};
recover.onclick = async () => {
  recover.disabled = true;
  try {
    const run = await api('/v1/lab/recovery',{subject_id:currentSubject});
    statusText.textContent = 'Workflow running: snapshot → erasure → private restore → verification → release.';
    const timer = setInterval(async () => {
      try {
        const value = await api('/v1/runs/'+run.run_id);
        result.textContent = JSON.stringify(value,null,2);
        if (value.status !== 'RUNNING') {
          clearInterval(timer);
          statusText.textContent = value.status === 'SUCCEEDED' ? 'Guarded recovery completed. Review the verification results.' : 'Recovery held. Review project monitoring before retrying.';
          seed.disabled = false;
        }
      } catch(error) { clearInterval(timer); statusText.textContent = error.message; }
    },15000);
  } catch(error) { statusText.textContent = error.message; seed.disabled = false; }
};
callback().catch(error => { statusText.textContent = error.message; });
