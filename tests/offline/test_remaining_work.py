"""Dependency-free acceptance checks. Run unittest discover -s tests/offline -v."""
import copy
import sqlite3
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from backend.local_runtime import (create_local_candidate_snapshot, local_candidate_runtime,
                                   local_runtime, temporary_local_directory)
from backend.eraselock.domain import AdmissionClosed, Conflict, EvidenceIncomplete, InflightUnresolved, StaleEpoch
from backend.eraselock.local_security import LocalEvidenceSigner, issue_token, verify_token
from backend.eraselock.workflows import RecoveryWorkflow
from contracts.adapter_protocol import SubjectScope
from scripts.offline_demo import demo


class OfflineAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_local_directory()
        self.root = self.temp.__enter__()
        self.addCleanup(self.temp.__exit__, None, None, None)
        self.key = b"e" * 32
        self.tasks = local_runtime(self.root, self.key)
        self.scope = SubjectScope("tenant", "person", 1)
        self.tasks.authority.register_subject(self.scope)
        for i, adapter in enumerate(self.tasks.adapters):
            adapter.put(self.scope, "doc", 1, "synthetic", f"seed-{i}")
        create_local_candidate_snapshot(self.root, "restore")
        self.candidate_tasks = local_candidate_runtime(self.root, "restore", self.key)

    def erase(self):
        return self.tasks.erase({"scope": asdict(self.scope), "request_id": "erase-1"})

    def test_full_erasure_verifies_every_store(self):
        result = self.erase()
        self.assertEqual(result["state"], "VERIFIED")
        self.assertTrue(self.tasks.signer.verify(result["evidence"]))
        self.assertEqual(len(result["evidence"]["certificate"]["checks"]), 3)
        for adapter in self.tasks.adapters:
            self.assertEqual(adapter.discover(self.scope), [])

    def test_restart_and_repeated_request(self):
        first = self.erase()
        self.tasks = local_runtime(self.root, self.key)
        second = self.erase()
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(second["state"], "VERIFIED")

    def test_unresolved_permit_holds_before_remediation(self):
        self.tasks.authority.acquire_permit(self.scope, "pending")
        with self.assertRaises(InflightUnresolved):
            self.erase()
        self.assertEqual(len(self.tasks.adapters[0].discover(self.scope)), 1)
        self.assertEqual(self.tasks.adapters[0].public_read(self.scope), [])

    def test_crash_after_durable_write_is_reconciled(self):
        with patch.object(self.tasks.authority, "close_permit", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.tasks.adapters[0].put(self.scope, "later", 2, "synthetic", "crash-write")
        self.assertEqual(len(self.tasks.authority.unresolved_permits(self.scope)), 1)
        self.assertEqual(self.erase()["state"], "VERIFIED")

    def test_stale_event_cannot_recreate_data(self):
        self.erase()
        with self.assertRaises(AdmissionClosed):
            self.tasks.adapters[0].put(self.scope, "doc", 2, "old event", "replay")
        self.assertEqual(self.tasks.adapters[0].discover(self.scope), [])

    def test_failure_holds_and_retry_remediates(self):
        with patch.object(self.tasks.adapters[1], "remediate", side_effect=OSError("offline failure")):
            result = self.erase()
        self.assertEqual(result["state"], "EXCEPTION")
        self.assertEqual(result["evidence"]["certificate"]["decision"], "HOLD")
        self.assertEqual(self.erase()["state"], "VERIFIED")

    def test_reverification_failure_revokes_verified_status(self):
        self.erase()
        with patch.object(self.tasks.adapters[0], "verify", side_effect=OSError("unavailable")):
            self.assertEqual(self.erase()["state"], "EXCEPTION")
        self.assertEqual(self.erase()["state"], "VERIFIED")

    def test_wrong_scope_evidence_cannot_pass(self):
        original = self.tasks.adapters[0].verify
        with patch.object(self.tasks.adapters[0], "verify", side_effect=lambda s, e:
                          replace(original(s, e), scope=SubjectScope("tenant", "other", 1))):
            self.assertEqual(self.erase()["state"], "EXCEPTION")

    def test_other_subject_is_preserved(self):
        other = SubjectScope("tenant", "other", 1)
        self.tasks.authority.register_subject(other)
        self.tasks.adapters[0].put(other, "doc", 1, "allowed", "other-seed")
        self.erase()
        self.assertEqual(len(self.tasks.adapters[0].public_read(other)), 1)

    def test_evidence_tamper_and_wrong_key(self):
        envelope = self.erase()["evidence"]
        changed = copy.deepcopy(envelope)
        changed["certificate"]["decision"] = "FORGED"
        self.assertFalse(self.tasks.signer.verify(changed))
        self.assertFalse(LocalEvidenceSigner(b"x" * 32).verify(envelope))

    def test_tokens_bind_identity_and_expire(self):
        token = issue_token(self.key, "tenant", "person", ttl=10, now=100)
        self.assertEqual(verify_token(self.key, token, now=101)["subject_id"], "person")
        for now in (99, 110):
            with self.assertRaises(ValueError):
                verify_token(self.key, token, now=now)
        with self.assertRaises(ValueError):
            verify_token(b"x" * 32, token, now=101)
        with self.assertRaises(ValueError):
            verify_token(self.key, token + "0", now=101)

    def test_weak_keys_rejected(self):
        with self.assertRaises(ValueError):
            issue_token(b"weak", "tenant", "person")

    def test_audit_survives_restart(self):
        self.erase()
        restarted = local_runtime(self.root, self.key)
        summary = restarted.audit.summary()
        self.assertEqual(summary[0]["task"], "erase")
        self.assertEqual(summary[0]["state"], "VERIFIED")
        self.assertEqual(summary[0]["count"], 1)

    def test_failed_task_records_hold(self):
        with self.assertRaises(ValueError):
            self.tasks.erase({"bad": "input"})
        self.assertEqual(self.tasks.audit.summary()[0]["state"], "HOLD")

    def test_recovery_requires_complete_authority_scope(self):
        self.erase()
        other = SubjectScope("tenant", "other", 1)
        self.tasks.authority.register_subject(other)
        self.tasks.erasure.run(other, "erase-other")
        with self.assertRaises(EvidenceIncomplete):
            RecoveryWorkflow(self.tasks.authority, self.tasks.adapters).validate_candidate(
                "incomplete", "tenant", [self.scope])

    def test_release_rechecks_current_epoch(self):
        self.erase()
        approval = self.candidate_tasks.validate({"candidate_id": "restore", "tenant_id": "tenant"})
        other = SubjectScope("tenant", "other", 1)
        self.tasks.authority.register_subject(other)
        self.tasks.authority.close_admission(other, "erase-new")
        with self.assertRaises(StaleEpoch):
            self.candidate_tasks.release({"candidate_id": "restore", "scope_digest": approval["scope_digest"]})

    def test_release_rejects_forged_digest_and_reuse(self):
        self.erase()
        approval = self.candidate_tasks.validate({"candidate_id": "restore", "tenant_id": "tenant"})
        with self.assertRaises(Conflict):
            self.candidate_tasks.release({"candidate_id": "restore", "scope_digest": "forged"})
        event = {"candidate_id": "restore", "scope_digest": approval["scope_digest"]}
        self.assertEqual(self.candidate_tasks.release(event)["state"], "RELEASED")
        with self.assertRaises(Conflict):
            self.candidate_tasks.release(event)

    def test_recovery_task_cannot_validate_live_or_substitute_candidate(self):
        self.erase()
        event = {"candidate_id": "restore", "tenant_id": "tenant"}
        with self.assertRaises(ValueError):
            self.tasks.validate(event)
        with self.assertRaises(ValueError):
            self.candidate_tasks.validate({**event, "candidate_id": "another-restore"})
        with self.assertRaises(ValueError):
            self.tasks.release({"candidate_id": "restore", "scope_digest": "forged"})
        self.assertEqual(len(self.candidate_tasks.adapters[0].discover(self.scope)), 1)
        approval = self.candidate_tasks.validate(event)
        self.assertTrue(approval["approved"])
        self.assertEqual(self.candidate_tasks.adapters[0].discover(self.scope), [])

    def test_candidate_runtime_rejects_missing_and_live_store_paths(self):
        with self.assertRaises(FileNotFoundError):
            local_candidate_runtime(self.root, "missing", self.key)
        with self.assertRaises(ValueError):
            local_candidate_runtime(self.root, "../data", self.key)
        with self.assertRaises(FileExistsError):
            create_local_candidate_snapshot(self.root, "restore")

    def test_request_cannot_replace_adapter_manifest(self):
        with self.assertRaises(ValueError):
            self.tasks.erase({"scope": asdict(self.scope), "request_id": "bad", "adapters": []})

    def test_old_snapshot_is_reconciled_without_network(self):
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")):
            result = demo()
        self.assertEqual(result["baseline_raw_rows"], 3)
        self.assertEqual(result["protected_raw_rows"], 0)
        self.assertEqual(result["release_state"], "RELEASED")
        self.assertTrue(result["evidence_authenticated"])


if __name__ == "__main__":
    unittest.main()
