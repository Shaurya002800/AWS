# Offline implementation checkpoint — 19 October 2026

The work in this checkpoint uses no AWS services, credentials, CLI calls, or
deployment commands. It is a local implementation checkpoint, not completion
of the entire blueprint. Source was copied from the supplied ZIP; this working
directory has no Git history or authenticated remote, and nothing was pushed.

## Implemented behavior

- The erase API runs admission closure, permit checks, commit, remediation,
  verification, and evidence creation through its configured adapter manifest.
  An unconfigured API returns 503. Unresolved writes return a conflict and keep
  admission closed. Adapter failures return EXCEPTION/HOLD, never VERIFIED.
- A previously verified decision is independently checked on retry. Failed,
  missing, stale, or mismatched evidence revokes its verified status.
- Recovery validation compares its scope with the independent authority's full
  erased-generation inventory. Release reloads the approval, checks its digest,
  expiry, and current epoch, and rejects reused approvals. This is an authority
  release decision; it does not switch a database endpoint or production route.
- Signed local sessions bind tenant and subject to issuer/audience/expiry.
  Identity headers cannot override these claims. The old shared-token/header
  mechanism requires explicitly selecting `RGX_AUTH_MODE=demo`.
- HMAC-SHA256 envelopes authenticate evidence using a separate local secret.
  The inner certificate remains accurately labelled UNSIGNED_LOCAL; the outer
  envelope names its local HMAC algorithm. This is shared-key authentication,
  not a public-key signature, KMS signature, or proof of physical deletion.
- Durable synthetic SQLite adapters provide three clearly named local stores:
  local-records, local-object-versions, and local-index. They are not substitutes
  for measured PostgreSQL, real S3 versioning, or an AI retrieval engine. The
  existing PostgreSQL/object simulator/index adapters remain available.
- Dependency-injected callable tasks implement erase, validate, and release.
  They never initialize an AWS SDK. Task inputs cannot select adapters or
  connection targets. Candidate validation tasks must be constructed with
  candidate-scoped adapters, never live-store adapters.
- Local task audit counts and durations survive restart and omit request data.
  Reference state machines hold on failure and use a final epoch-checked release.
  The invocation policy restricts calls to the three configured task ARNs.

## Run now, with no package installation

From the project directory in PowerShell:

```powershell
python -m unittest discover -s tests/offline -v
python -m scripts.offline_demo
```

The restore example creates an old SQLite snapshot, erases live data, then
reconciles and verifies the candidate before release. It measures one run and
compares raw rows using an independent SQL inspection. This is not an exposure
rate across the four original PostgreSQL scenarios. Its ephemeral signing key
is discarded after the demonstration checks the envelope.

## Run the HTTP API when dependencies are available

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[dev]'
$env:RGX_ENV = 'local'
$env:RGX_AUTH_MODE = 'signed'
$env:RGX_LOCAL_SIGNING_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
$env:RGX_LOCAL_EVIDENCE_KEY = (python -c "import secrets; print(secrets.token_hex(32))")
python -m scripts.local_token --tenant t01 --subject p017
python -m backend.api.dev_server
```

Use the printed token as `Authorization: Bearer <token>` and send the original
erase request for `t01/p017`, generation 4. The configured local adapters start
empty; the offline demo seeds data for its separate temporary experiment. The
server binds only to 127.0.0.1. Local session tokens expire after 15 minutes.
Keep the evidence key separately if retained envelopes need verification after
restart. Do not commit secrets. Run `python -m scripts.local_metrics` to inspect
task summaries after API operations. These credentials are for local work only.

## Verification and remaining work

The dependency-free acceptance suite checks full deletion, restart/retry,
unresolved permits, stale replay, adapter failure/retry, re-verification failure,
wrong-scope evidence, other-subject isolation, forged/expired tokens, evidence
tampering, incomplete recovery scope, epoch changes, digest mismatch, approval
reuse, adapter-input injection, and a restore with socket connections forbidden.

The existing FastAPI/PostgreSQL pytest suite must be rerun in an environment
with its dependencies and local PostgreSQL. Dependency download failed on this
machine; its old 49-test result is historical, not a current verification claim.
HTTP tests were updated for the new workflow and signed-session behavior.

Remaining: production OIDC/JWT integration; asymmetric/KMS signing and key
rotation; deployable Lambda packaging and trusted candidate registration;
complete service IAM/network policies and monitoring; actual isolated RDS
restore/publication; end-to-end AWS tests, cost measurements, and live evidence.
The files in `infra/workflows` are offline reference definitions with unresolved
ARN substitutions, not a complete deployable stack. No live capability is
claimed for them. AWS setup remains explicitly deferred by the user.
