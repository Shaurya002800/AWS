"""Opt-in live test: python -m scripts.aws_smoke. Uses only synthetic data.

Requires a deployed core stack, active FREE plan, and AWS login. Generated
resource identifiers/evidence remain in the ignored private evidence directory.
"""
import json
import os
import secrets
from pathlib import Path
from dataclasses import asdict
from uuid import uuid4
from backend.aws_runtime import aws_runtime
from backend.eraselock.domain import AdmissionClosed
from contracts.adapter_protocol import SubjectScope


def main():
    runtime = aws_runtime('.local-authority/aws', secrets.token_bytes(32),
                          profile=os.environ.get('RGX_AWS_PROFILE', 'rewindguard'))
    scope = SubjectScope('synthetic', 'smoke_' + uuid4().hex, 1)
    runtime.tasks.authority.register_subject(scope)
    runtime.records.guarded_upsert_profile(scope, operation_id='seed_' + uuid4().hex,
                                           source_version=1, display_name='Synthetic AWS Demo')
    document = runtime.put_new_document(scope, b'Synthetic RewindGuard cloud smoke document')
    assert runtime.objects.public_get(scope, document['key']) is not None
    outcome = runtime.tasks.erase({'scope': asdict(scope), 'request_id': 'erase_' + uuid4().hex})
    assert outcome['state'] == 'VERIFIED', outcome['state']
    assert runtime.objects.discover(scope) == []
    assert runtime.objects.public_get(scope, document['key']) is None
    assert runtime.records.public_read(scope) == []
    try:
        runtime.put_new_document(scope, b'Forbidden resurrection')
    except AdmissionClosed:
        stale_write_blocked = True
    else:
        raise AssertionError('Erased subject accepted a new S3 write')
    message_id = runtime.send_profile(scope, 'Forbidden delayed event', source_version=2)
    results = []
    # Bound the test's request count and wall time. No always-running worker.
    for _ in range(3):
        results.extend(runtime.worker.consume_once(wait_seconds=2))
        if any(item.message_id == message_id and item.result.value == 'REJECTED_ERASED' for item in results):
            break
    replay_blocked = any(item.message_id == message_id and item.result.value == 'REJECTED_ERASED' for item in results)
    assert replay_blocked, 'SQS stale-event rejection was not observed within the bounded polling window'
    report = {'mode': 'HYBRID_AWS_CORE_LOCAL_RECORDS', 'region': 'ap-southeast-2',
              'erasure_state': outcome['state'], 's3_versions_remaining': 0,
              'stale_s3_write_blocked': stale_write_blocked, 'sqs_replay_blocked': replay_blocked,
              'evidence_authenticated': runtime.tasks.signer.verify(outcome['evidence']),
              'limits': 'Records and index remain local. No RDS, Lambda, or hosted API.',
              'evidence': outcome['evidence']}
    path = Path('evidence/private/aws-smoke.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key != 'evidence'}, indent=2))


if __name__ == '__main__':
    main()
