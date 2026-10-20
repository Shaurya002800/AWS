# Notebook UI redesign

The hosted demo now follows the supplied reference's notebook/scrapbook direction:
ruled cream paper, oversized condensed headings, outlined labels, taped polaroid
illustrations, mint/pink/blue/yellow stickers, and black/yellow folder panels.
The reference itself is not copied into the public repository.

## Interaction changes

- Sign-in and lab navigation are easy to find. Three numbered cards explain
  authentication, synthetic data creation, and guarded recovery.
- Controls remain disabled until their actual prerequisites are met.
- Evidence starts empty and shows the profile/document/index counts actually
  returned by the run API. A held result is distinct from a successful release.
- The signing label reports KMS signature generation; it does not falsely claim
  the browser performed cryptographic verification.
- Run JSON can be downloaded after a terminal workflow result. Status polling
  is sequential, avoiding overlapping requests. Unknown status does not enable
  a duplicate recovery run.
- PKCE, issuer/audience authorization, in-memory tokens, operator checks and
  existing API routes remain in place. No external fonts/images/scripts or new
  permissions are introduced. The offline dashboard retains its stylesheet.
- The layout adapts to mobile, supports keyboard focus and skip navigation,
  polite status announcements, and reduced-motion preferences.

## Validation

Six dependency-free frontend behavior tests cover signed-out empty states,
operator seed/recovery/evidence, non-operator controls, PKCE state rejection,
paused-database failure, and held recovery. Run with:

~~~sh
node --test tests/frontend/cloud_ui.test.cjs
~~~

The Python suite passes 79 tests; 16 PostgreSQL integration tests are skipped
without a local DSN and run against PostgreSQL in GitHub CI. OpenAPI regeneration
produced no diff. The Linux Lambda bundle contains the new cloud stylesheet.
Browser DOM checks found no horizontal overflow at mobile and desktop widths
(375px and 1425px content widths); expandable lab guidance worked. Browser
screenshot capture was unavailable in this session, so no screenshot-based
pixel comparison is claimed.

## Publishing

~~~powershell
.venv/Scripts/python.exe -m scripts.deploy_cloud_ui
~~~

This updates code in the existing stack with bootstrap disabled. It checks the
active Free plan and positive credits, uploads the application bundle, retains
the infrastructure template, checks hosted assets, and reads database standby
states. It never starts or creates a database. No recovery experiment is needed
for an appearance-only deployment.
