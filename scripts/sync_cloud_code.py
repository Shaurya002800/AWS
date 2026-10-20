"""Reconcile a tested emergency code patch into the stack parameters."""
import json
from scripts.deploy_application import session,sdk,stack_outputs,STACK,wait_stack

def sync(sess):
    cf=sdk(sess,'cloudformation')
    info,_=stack_outputs(cf,STACK)
    if info['StackStatus'] not in {'CREATE_COMPLETE','UPDATE_COMPLETE'}:
        raise RuntimeError('Wait for the current stack operation to finish')
    patch=json.loads(open('evidence/private/code-patch.json',encoding='utf-8').read())
    values={'CodeKey':patch['key'],'CodeVersion':patch['version']}
    parameters=[{'ParameterKey':p['ParameterKey'],'ParameterValue':values[p['ParameterKey']]} if p['ParameterKey'] in values else {'ParameterKey':p['ParameterKey'],'UsePreviousValue':True} for p in info['Parameters']]
    cf.update_stack(StackName=STACK,UsePreviousTemplate=True,Parameters=parameters,Capabilities=['CAPABILITY_IAM'])
    wait_stack(cf,'update')
    print({'status':'stack code synchronized; bootstrap remains disabled'},flush=True)

if __name__=='__main__':
    sync(session())
