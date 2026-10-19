"""Issue a short-lived local session using the operator's shell secret."""
import argparse
import os
from backend.eraselock.local_security import issue_token


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--subject", required=True)
    args = parser.parse_args()
    print(issue_token(os.environ.get("RGX_LOCAL_SIGNING_KEY", "").encode(), args.tenant, args.subject))
