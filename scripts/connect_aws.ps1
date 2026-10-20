param([switch]$Login)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$profileName = 'rewindguard'
$projectRegion = 'ap-southeast-2'
$stackName = 'rewindguard-x-core'
$awsExecutable = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $awsExecutable) { $awsExecutable = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe' }
if (-not (Test-Path -LiteralPath $awsExecutable)) { throw 'AWS CLI is not installed.' }
function Invoke-AwsJson {
    $response = & $awsExecutable @args --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw 'AWS command failed. No further deployment actions will run.' }
    return (($response -join "`n") | ConvertFrom-Json)
}
Push-Location -LiteralPath $projectRoot
try {
    if ($Login) {
        & $awsExecutable login --profile $profileName --region $projectRegion
        if ($LASTEXITCODE -ne 0) { throw 'AWS browser login did not complete.' }
    }
    $plan = Invoke-AwsJson freetier get-account-plan-state --profile $profileName --region us-east-1 --output json
    if ($plan.accountPlanType -ne 'FREE' -or $plan.accountPlanStatus -ne 'ACTIVE' -or
        $plan.accountPlanRemainingCredits.unit -ne 'USD' -or $plan.accountPlanRemainingCredits.amount -lt 10) {
        throw 'Held: active FREE plan with at least USD 10 remaining credits is required. Never upgrade the plan for this script.'
    }
    $summary = Invoke-AwsJson cloudformation list-stacks --profile $profileName --region $projectRegion --output json
    $existing = @($summary.StackSummaries | Where-Object { $_.StackName -eq $stackName -and $_.StackStatus -ne 'DELETE_COMPLETE' })
    if ($existing.Count -gt 0) {
        $details = Invoke-AwsJson cloudformation describe-stacks --stack-name $stackName --profile $profileName --region $projectRegion --output json
        $stack = $details.Stacks[0]
        if (-not ($stack.Tags | Where-Object { $_.Key -eq 'Project' -and $_.Value -eq 'rewindguard-x' })) { throw 'Stack ownership tag does not match; refusing to modify it.' }
        if ($stack.StackStatus -notin @('CREATE_COMPLETE','UPDATE_COMPLETE')) { throw "Existing stack needs inspection: $($stack.StackStatus)" }
        Write-Host 'Using the existing project core stack.'
    } else {
        $changeName = 'connect-' + (Get-Date -Format 'yyyyMMddHHmmss')
        $null = Invoke-AwsJson cloudformation create-change-set --stack-name $stackName --change-set-name $changeName --change-set-type CREATE --template-body file://infra/core.json --tags Key=Project,Value=rewindguard-x Key=Purpose,Value=synthetic-course-demo --profile $profileName --region $projectRegion --output json
        & $awsExecutable cloudformation wait change-set-create-complete --stack-name $stackName --change-set-name $changeName --profile $profileName --region $projectRegion
        if ($LASTEXITCODE -ne 0) { throw 'Change-set creation failed; no execution attempted.' }
        $change = Invoke-AwsJson cloudformation describe-change-set --stack-name $stackName --change-set-name $changeName --profile $profileName --region $projectRegion --output json
        $expected = @('AuthorityTable','DocumentBucket','DeadLetterQueue','ProfileEventQueue')
        if ($change.Changes.Count -ne 4) { throw 'Unexpected resource count; inspect the change set before proceeding.' }
        foreach ($item in $change.Changes) {
            if ($item.ResourceChange.Action -ne 'Add' -or $item.ResourceChange.LogicalResourceId -notin $expected) { throw 'Unexpected resource change; refusing to execute.' }
        }
        Write-Host 'Deploying only the four private, tagged core resources in Sydney.'
        & $awsExecutable cloudformation execute-change-set --stack-name $stackName --change-set-name $changeName --profile $profileName --region $projectRegion
        if ($LASTEXITCODE -ne 0) { throw 'Change-set execution failed.' }
        & $awsExecutable cloudformation wait stack-create-complete --stack-name $stackName --profile $profileName --region $projectRegion
        if ($LASTEXITCODE -ne 0) { throw 'Stack did not complete. Inspect CloudFormation events before retrying.' }
    }
    $pythonExecutable = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonExecutable)) { throw 'Create the project .venv and install .[dev,aws] before running the smoke test.' }
    & $pythonExecutable -m scripts.aws_smoke
    if ($LASTEXITCODE -ne 0) { throw 'Core resources exist, but live integration verification failed. Preserve the error for inspection.' }
    Write-Host 'AWS CORE CONNECTED AND VERIFIED. Records/index/API execution remain local.'
    Write-Host 'Private evidence: evidence/private/aws-smoke.json. Resources remain for your next run.'
} finally { Pop-Location }
