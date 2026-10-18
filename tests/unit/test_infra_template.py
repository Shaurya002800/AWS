import json
from pathlib import Path


def test_cloud_template_has_private_versioned_storage_and_no_compute():
    template = json.loads((Path(__file__).resolve().parents[2] / "infra" / "core.json").read_text())
    resources = template["Resources"]
    assert resources["AuthorityTable"]["Properties"]["BillingMode"] == "PAY_PER_REQUEST"
    bucket = resources["DocumentBucket"]["Properties"]
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert resources["ProfileEventQueue"]["Properties"]["RedrivePolicy"]["maxReceiveCount"] == 3
    assert {item["Type"] for item in resources.values()} == {
        "AWS::DynamoDB::Table", "AWS::S3::Bucket", "AWS::SQS::Queue"
    }
