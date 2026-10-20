"""Asymmetric KMS evidence signatures, verifiable with the exported public key."""
import base64
from dataclasses import asdict
from hashlib import sha256
from backend.cloud.common import canonical
from backend.eraselock.evidence import digest_payload


class KmsEvidenceSigner:
    def __init__(self,kms,key_id):
        self.kms,self.key_id=kms,key_id

    def sign(self,certificate):
        if not certificate.verify_integrity():
            raise ValueError('Invalid inner certificate')
        inner=asdict(certificate)
        inner['signature_status']='KMS_ECDSA_SHA_256'
        unsigned=dict(inner)
        unsigned.pop('integrity')
        inner['integrity']=digest_payload(unsigned)
        public=self.kms.get_public_key(KeyId=self.key_id)
        payload={'schema':'rgx-kms-evidence-1','algorithm':'ECDSA_SHA_256',
                 'key_id':public['KeyId'],'public_key_spki_der':base64.b64encode(public['PublicKey']).decode(),
                 'certificate':inner}
        result=self.kms.sign(KeyId=self.key_id,Message=sha256(canonical(payload)).digest(),
                             MessageType='DIGEST',SigningAlgorithm='ECDSA_SHA_256')
        return {**payload,'signature':base64.b64encode(result['Signature']).decode()}

    def verify(self,envelope):
        try:
            if envelope.get('algorithm')!='ECDSA_SHA_256' or envelope.get('schema')!='rgx-kms-evidence-1':
                return False
            public=self.kms.get_public_key(KeyId=self.key_id)
            if envelope.get('key_id')!=public['KeyId'] or base64.b64decode(envelope['public_key_spki_der'])!=public['PublicKey']:
                return False
            payload={k:v for k,v in envelope.items() if k!='signature'}
            response=self.kms.verify(KeyId=self.key_id,Message=sha256(canonical(payload)).digest(),
                                     MessageType='DIGEST',SigningAlgorithm='ECDSA_SHA_256',
                                     Signature=base64.b64decode(envelope['signature']))
            return bool(response.get('SignatureValid'))
        except (ValueError,KeyError,TypeError):
            return False
        except Exception as error:
            if getattr(error,'response',{}).get('Error',{}).get('Code')=='KMSInvalidSignatureException':
                return False
            raise
