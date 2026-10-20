$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$env:AWS_LOGIN_CACHE_DIRECTORY = Join-Path $projectRoot '.local-authority\login-cache'
New-Item -ItemType Directory -Path $env:AWS_LOGIN_CACHE_DIRECTORY -Force | Out-Null
$awsExecutable = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $awsExecutable) { $awsExecutable = Join-Path $env:USERPROFILE 'AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe' }
& $awsExecutable login --profile rewindguard --region ap-southeast-2
if ($LASTEXITCODE -ne 0) { throw 'AWS login did not complete.' }
Write-Host 'Project login refreshed. Credentials are confined to the ignored local login cache.'
