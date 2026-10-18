# RewindGuard X contributor instructions

Read `docs/RewindGuard_X_Complete_Technical_Blueprint.docx`, `docs/TEAM_EXECUTION_PLAN.md`, `docs/PROJECT_CONFIG.md`, and `docs/STATUS.md` before changing code. This is a three-person AWS course project with a zero out-of-pocket spending rule.

The shared remote is `https://github.com/Shaurya002800/AWS.git`; the integration branch is `main`. Work on a feature branch and open a PR. Never force push `main`. Keep `docs/STATUS.md` current at a handoff. The plan's role ownership and phase gates are authoritative for contributor work; clarify or propose a contract change through a focused PR if implementation requires a deviation.

Member 1 alone may connect to or deploy into the AWS account. Members 2 and 3 must work locally without AWS credentials. Cloud deployment requires Member 1 to verify account eligibility, active credits/free usage, Region, cost estimate, and teardown. Do not add credentials or generated private evidence to Git.

Use synthetic data. Develop and test locally before AWS integration. Treat the Word blueprint as a specification, not evidence that anything has already been built or deployed. Report tested paths and limitations precisely. A completed implementation should include runnable tests and updated documentation; a scaffold alone does not complete a milestone.
