"""Cloud EraseLock composition over VPC data workers and KMS signatures."""
import os
from dataclasses import asdict
from backend.cloud.common import client,authority,invoke
from backend.cloud.signing import KmsEvidenceSigner
from backend.adapters.aws_s3 import AwsS3Adapter
from backend.eraselock.workflows import ErasureWorkflow,RecoveryWorkflow
from contracts.adapter_protocol import AdapterEvidence,CheckResult,SubjectScope


class RemoteAdapter:
    def __init__(self,name,function,candidate_id=None):
        self.name,self.function,self.candidate_id=name,function,candidate_id

    def _call(self,action,scope,**fields):
        event={'action':action,'scope':asdict(scope),'adapter':self.name,**fields}
        if self.candidate_id: event['candidate_id']=self.candidate_id
        return invoke(self.function,event)

    def remediate(self,scope,decision_id):
        self._call('remediate',scope,decision_id=decision_id)

    def verify(self,scope,authority_epoch):
        data=self._call('verify',scope,authority_epoch=authority_epoch)
        return AdapterEvidence(**{**data,'scope':SubjectScope(**data['scope']),'result':CheckResult(data['result'])})

    def reconcile_committed(self,scope):
        self._call('reconcile',scope)


def adapters(auth,candidate_id=None):
    function=os.environ['CANDIDATE_FUNCTION'] if candidate_id else os.environ['DATA_FUNCTION']
    return [RemoteAdapter('rds',function,candidate_id),RemoteAdapter('index',function,candidate_id),
            AwsS3Adapter(client('s3'),os.environ['DOCUMENT_BUCKET'],auth,allow_mutation=True)]


def signer():
    return KmsEvidenceSigner(client('kms'),os.environ['EVIDENCE_KEY'])


def erase(scope,request_id):
    auth=authority()
    stores=adapters(auth)
    stores[0].reconcile_committed(scope)
    outcome=ErasureWorkflow(auth,stores).run(scope,request_id)
    return {'decision_id':outcome.decision_id,'state':outcome.state.value,'evidence':signer().sign(outcome.certificate)}


def validate(candidate_id,tenant_id):
    auth=authority()
    outcome=RecoveryWorkflow(auth,adapters(auth,candidate_id)).validate_candidate(candidate_id,tenant_id,auth.erased_scopes(tenant_id))
    return {'candidate_id':candidate_id,'approved':outcome.approved,'scope_digest':outcome.scope_digest,
            'validated_epoch':outcome.validated_epoch,'evidence':signer().sign(outcome.certificate)}
