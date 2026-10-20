"""Hybrid course runtime: AWS authority/objects/queue; local records and index.

No AWS clients are created at import. This is deliberately not an RDS or
Lambda deployment. Only the named project stack in Sydney is accepted.
"""
from dataclasses import dataclass
from pathlib import Path
from contextlib import closing
import json
from uuid import uuid4

from backend.adapters.aws_s3 import AwsS3Adapter
from backend.adapters.sqlite_local import SQLiteLocalAdapter
from backend.adapters.postgres import WriteOutcome
from backend.eraselock.dynamodb_authority import DynamoDBAuthority
from backend.eraselock.domain import AdmissionClosed
from backend.eraselock.local_security import LocalEvidenceSigner
from backend.eraselock.local_monitoring import LocalAuditLog
from backend.orchestration.handlers import WorkflowTasks
from backend.workers.aws_sqs import AwsSQSProfileWorker

REGION = "ap-southeast-2"
STACK = "rewindguard-x-core"


def check_free_plan(client):
    plan = client.get_account_plan_state()
    credits = plan.get("accountPlanRemainingCredits", {})
    if (plan.get("accountPlanType") != "FREE" or plan.get("accountPlanStatus") != "ACTIVE"
            or credits.get("unit") != "USD" or credits.get("amount", 0) < 10):
        raise RuntimeError("Cloud access held: active Free plan with at least USD 10 credits required")
    return plan


class LocalQueueProfiles(SQLiteLocalAdapter):
    """SQS worker contract implemented over local synthetic records."""
    def guarded_upsert_profile(self, scope, *, operation_id, source_version, display_name):
        if not self.authority.may_read(scope):
            raise AdmissionClosed("Subject generation is closed")
        with closing(self._connect()) as conn:
            replayed = conn.execute("SELECT 1 FROM local_journal WHERE operation=?", (operation_id,)).fetchone() is not None
        self.put(scope, "profile", source_version, display_name, operation_id)
        return WriteOutcome(operation_id, "COMMITTED", source_version, replayed)


@dataclass
class AwsRuntime:
    tasks: WorkflowTasks
    records: LocalQueueProfiles
    objects: AwsS3Adapter
    worker: AwsSQSProfileWorker
    sqs: object
    queue_url: str

    def send_profile(self, scope, display_name, source_version=1):
        from datetime import datetime, timezone
        event_id = uuid4().hex
        body = {"schema_version": "1.0", "operation_type": "PROFILE_UPSERT",
                "event_id": event_id, "tenant_id": scope.tenant_id, "subject_id": scope.subject_id,
                "subject_generation": scope.subject_generation, "source_version": source_version,
                "display_name": display_name, "authority_epoch_seen": self.tasks.authority.epoch(scope.tenant_id),
                "occurred_at": datetime.now(timezone.utc).isoformat(), "trace_id": event_id}
        result = self.sqs.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(body))
        return result["MessageId"]

    def put_new_document(self, scope, content: bytes):
        """A new immutable document, with a fresh permit and unique key each call.

        This API is not an update/idempotency API. A crash leaves its permit
        unresolved; it must not be automatically declared failed.
        """
        if not isinstance(content, bytes) or not 0 < len(content) <= 65536:
            raise ValueError("Synthetic documents must be 1..65536 bytes")
        operation_id = "s3-" + uuid4().hex
        permit = self.tasks.authority.acquire_permit(scope, operation_id)
        key = self.objects.prefix(scope) + operation_id
        result = self.objects.client.put_object(Bucket=self.objects.bucket, Key=key,
                                               Body=content, ContentType="text/plain",
                                               Metadata={"operation-id": operation_id})
        self.tasks.authority.close_permit(permit.permit_id, outcome="COMMITTED")
        return {"key": key, "version_id": result["VersionId"]}


def aws_runtime(directory, evidence_key, *, profile="rewindguard", region=REGION, session=None):
    if region != REGION:
        raise ValueError("The project is restricted to ap-southeast-2")
    if session is None:
        import boto3
        session = boto3.Session(profile_name=profile, region_name=region)
    check_free_plan(session.client("freetier", region_name="us-east-1"))
    stacks = session.client("cloudformation", region_name=region).describe_stacks(StackName=STACK)["Stacks"]
    stack = stacks[0]
    tags = {tag["Key"]: tag["Value"] for tag in stack.get("Tags", [])}
    if tags.get("Project") != "rewindguard-x" or stack["StackStatus"] not in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}:
        raise RuntimeError("Expected healthy, project-tagged stack is unavailable")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    authority = DynamoDBAuthority(session.client("dynamodb", region_name=region), outputs["AuthorityTableName"])
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    records = LocalQueueProfiles(root / "data.sqlite3", authority, "local-records")
    index = SQLiteLocalAdapter(root / "data.sqlite3", authority, "local-index")
    objects = AwsS3Adapter(session.client("s3", region_name=region), outputs["DocumentBucketName"], authority,
                           allow_mutation=True)
    tasks = WorkflowTasks(authority, [records, index, objects], LocalEvidenceSigner(evidence_key),
                          LocalAuditLog(root / "audit.sqlite3"))
    sqs = session.client("sqs", region_name=region)
    worker = AwsSQSProfileWorker(sqs, outputs["ProfileEventQueueUrl"], outputs["DeadLetterQueueUrl"], records)
    return AwsRuntime(tasks, records, objects, worker, sqs, outputs["ProfileEventQueueUrl"])
