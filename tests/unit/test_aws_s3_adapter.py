import pytest

from backend.adapters.aws_s3 import AwsS3Adapter
from backend.eraselock.authority import InMemoryAuthority
from contracts.adapter_protocol import CheckResult, SubjectScope


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        assert kwargs["Bucket"] == "rgx-test"
        yield from self.pages


class FakeS3:
    def __init__(self):
        self.key = "t01/p017/g4/doc"
        self.versions = [
            {"Key": self.key, "VersionId": "v1"},
            {"Key": self.key, "VersionId": "v2"},
        ]
        self.markers = [{"Key": self.key, "VersionId": "m1"}]
        self.deleted = []

    def get_paginator(self, name):
        assert name == "list_object_versions"
        return FakePaginator([
            {"Versions": self.versions[:1]},
            {"Versions": self.versions[1:], "DeleteMarkers": self.markers},
        ])

    def delete_object(self, **kwargs):
        self.deleted.append(kwargs)
        self.versions = [v for v in self.versions if v["VersionId"] != kwargs["VersionId"]]
        self.markers = [v for v in self.markers if v["VersionId"] != kwargs["VersionId"]]


def test_s3_adapter_enumerates_versions_and_requires_explicit_mutation():
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    fake = FakeS3()
    adapter = AwsS3Adapter(fake, "rgx-test", authority)
    assert len(adapter.discover(scope)) == 2
    decision = authority.close_admission(scope, "erase-1")
    committed = authority.commit_erasure(decision.decision_id)
    assert adapter.verify(scope, committed.committed_epoch).result == CheckResult.FAIL
    with pytest.raises(RuntimeError):
        adapter.remediate(scope, decision.decision_id)
    adapter.allow_mutation = True
    adapter.remediate(scope, decision.decision_id)
    assert {item["VersionId"] for item in fake.deleted} == {"v1", "v2", "m1"}
    assert adapter.verify(scope, committed.committed_epoch).result == CheckResult.PASS


def test_authorized_retained_version_is_reported_as_restricted():
    scope = SubjectScope("t01", "p017", 4)
    authority = InMemoryAuthority()
    authority.register_subject(scope)
    fake = FakeS3()
    adapter = AwsS3Adapter(
        fake, "rgx-test", authority, allow_mutation=True,
        authorized_retained_versions={(fake.key, "v1")},
    )
    decision = authority.close_admission(scope, "erase-1")
    committed = authority.commit_erasure(decision.decision_id)
    adapter.remediate(scope, decision.decision_id)
    proof = adapter.verify(scope, committed.committed_epoch)
    assert proof.result == CheckResult.RESTRICTED
    assert adapter.public_get(scope, fake.key) is None
