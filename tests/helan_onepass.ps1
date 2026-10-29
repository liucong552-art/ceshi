#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$RunnerRef,
    [string]$Domain,
    [string]$AcmeEmail,
    [ValidateRange(60, 7200)][int]$CpuSeconds = 3600,
    [string]$SshHost = '162.211.231.219',
    [ValidateRange(1, 65535)][int]$SshPort = 22,
    [string]$SshKey = (Join-Path $env:USERPROFILE '.ssh\vps_root_ed25519'),
    [switch]$SelfTest
)
$ErrorActionPreference = 'Stop'
$onepassSelfTest = $SelfTest.IsPresent
$library = Join-Path $PSScriptRoot 'helan_round4.ps1'
if (-not $onepassSelfTest -or -not (Test-Path -LiteralPath $library)) {
    if ($RunnerRef -notmatch '^[0-9a-f]{40}$') { throw 'A pinned runner commit is required' }
    Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/liucong552-art/ceshi/' + $RunnerRef + '/tests/helan_round4.ps1') -OutFile $library -TimeoutSec 120
}
. $library -FunctionsOnly -RunnerRef $RunnerRef -SshHost $SshHost -SshPort $SshPort -SshKey $SshKey

function Parse-Onepass([string]$Text) {
    $match = [regex]::Match($Text, '(?s)HY2_ONEPASS_BEGIN\r?\n(.*?)\r?\nHY2_ONEPASS_END')
    if (-not $match.Success) { throw 'Server did not return a onepass status envelope' }
    try { return ($match.Groups[1].Value | ConvertFrom-Json) }
    catch { throw 'Invalid server status JSON' }
}
function Onepass-Ssh([string]$Body, [int]$TimeoutSeconds = 30) {
    return Parse-Onepass (Invoke-ProbeSsh $Body $TimeoutSeconds)
}
function Encode-Request($Value) {
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Value | ConvertTo-Json -Compress -Depth 15)))
}
if ($onepassSelfTest) {
    $parsed = Parse-Onepass "banner`nHY2_ONEPASS_BEGIN`n{`"state`":`"SERVER_PASS`",`"cases`":[]}`nHY2_ONEPASS_END"
    if ($parsed.state -ne 'SERVER_PASS') { throw 'Status envelope self-test failed' }
    $rejected = $false
    try { Parse-Onepass 'unframed invalid data' | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'Unframed status was accepted' }
    $encoded = Encode-Request @{ domain = 'hy2.example.com'; ref = ('a' * 40); cpu_seconds = 3600 }
    $back = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)) | ConvertFrom-Json
    if ($back.cpu_seconds -ne 3600) { throw 'Request transport self-test failed' }
    Write-Host 'PASS: onepass Windows request, framed status and import guards'
    return
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Run this entry in Windows PowerShell after leaving SSH' }
    if ($RunnerRef -notmatch '^[0-9a-f]{40}$') { throw 'A pinned runner commit is required' }
    if ([string]::IsNullOrWhiteSpace($Domain)) { $Domain = Read-Host 'Helan test domain' }
    if ([string]::IsNullOrWhiteSpace($AcmeEmail)) { $AcmeEmail = Read-Host 'Certificate email' }
    $Domain = $Domain.Trim().TrimEnd('.').ToLowerInvariant()
    $AcmeEmail = $AcmeEmail.Trim()
    if ($Domain -notmatch '^[a-z0-9.-]+$' -or $AcmeEmail -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { throw 'Invalid domain or email' }
    $script:Work = New-PrivateWork
    $request = @{ ref = $RunnerRef; domain = $Domain; email = $AcmeEmail; cpu_seconds = $CpuSeconds }
    $payload = Encode-Request $request
    $startScript = @'
set -Eeuo pipefail
[[ "$(hostname -s)" == helan && "$EUID" == 0 ]]
if ! command -v curl >/dev/null || ! command -v python3 >/dev/null; then
  umask 077
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -o Acquire::Retries=3 >/root/hy2-onepass-bootstrap.private.log 2>&1
  apt-get install -y --no-install-recommends curl ca-certificates python3 >>/root/hy2-onepass-bootstrap.private.log 2>&1
fi
curl -fLsS --retry 2 --connect-timeout 10 --max-time 120 'https://raw.githubusercontent.com/liucong552-art/ceshi/__REF__/tests/helan_onepass.py' -o /root/helan_hy2_onepass.py
printf '%s' '__REQUEST__' | base64 --decode | python3 -B /root/helan_hy2_onepass.py start
'@
    Write-Host 'Starting the fresh Debian 13 non-WG suite. One planned reboot; automatic reconnection.'
    Write-Host ('CPU sample: ' + $CpuSeconds + 's. Keep this PowerShell window open. Re-running the same entry resumes status.')
    $server = Onepass-Ssh ($startScript.Replace('__REF__', $RunnerRef).Replace('__REQUEST__', $payload)) 900
    $poll = 'python3 -B /root/helan_hy2_onepass.py status'
    $deadline = [DateTime]::UtcNow.AddSeconds($CpuSeconds + 5400)
    $lastPhase = ''; $lastPrint = [DateTime]::MinValue; $unavailable = 0
    while ([DateTime]::UtcNow -lt $deadline) {
        try { $server = Onepass-Ssh $poll 25; $unavailable = 0 }
        catch {
            $unavailable++
            if ($unavailable -gt 30) { throw 'SSH did not recover. Check the VPS console and the local ssh.private.log.' }
            Write-Host 'SSH temporarily unavailable; retrying automatically (including the planned reboot).'
            Start-Sleep -Seconds 15
            continue
        }
        if ($server.state -eq 'FAILED') { throw ($server.message + '; reports: ' + $server.report_directory) }
        if ($server.phase -ne $lastPhase -or ([DateTime]::UtcNow - $lastPrint).TotalSeconds -ge 60) {
            $passed = @($server.cases | Where-Object { $_.status -eq 'PASS' }).Count
            Write-Host ('[{0}] {1}; completed groups: {2}' -f $server.state, $server.phase, $passed)
            $lastPhase = $server.phase; $lastPrint = [DateTime]::UtcNow
        }
        if ($server.state -in @('SERVER_PASS', 'COMPLETE')) { break }
        Start-Sleep -Seconds 20
    }
    if ($server.state -notin @('SERVER_PASS', 'COMPLETE')) { throw 'Overall server acceptance deadline expired' }
    if ($server.state -ne 'COMPLETE') {
        Write-Host 'Server and reboot checks passed. Starting the actual Windows public HY2 client.'
        $captured = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $library -RunnerRef $RunnerRef -SshHost $SshHost -SshPort $SshPort -SshKey $SshKey 2>&1 | Tee-Object -FilePath (Join-Path $script:Work 'public-client.log')
        $clientCode = $LASTEXITCODE
        foreach ($line in $captured) { Write-Host ([string]$line) }
        if ($clientCode -ne 0) { throw 'Public client acceptance failed. Server results remain available for a resume.' }
        $resultLine = @($captured | ForEach-Object { [string]$_ } | Where-Object { $_ -match '^Result: ' }) | Select-Object -Last 1
        if ($null -eq $resultLine) { throw 'Public client result path is missing' }
        $clientResult = Get-Content -LiteralPath $resultLine.Substring(8).Trim() -Raw | ConvertFrom-Json
        $clientPayload = Encode-Request $clientResult
        $finish = "printf '%s' '__CLIENT__' | base64 --decode | python3 -B /root/helan_hy2_onepass.py finish"
        $server = Onepass-Ssh ($finish.Replace('__CLIENT__', $clientPayload)) 60
    }
    Write-Utf8 (Join-Path $script:Work 'summary.json') ($server | ConvertTo-Json -Depth 18)
    Write-Host ('PASS: configured non-WG acceptance complete; {0} checks. WG deferred.' -f $server.passed_checks)
    Write-Host ('Measured host CPU average: {0}% over {1}s (idle managed stack).' -f $server.cpu.average_host_cpu_percent, $server.cpu.seconds)
    Write-Host ('VPS reports: ' + $server.report_directory)
    Write-Host ('Windows summary: ' + (Join-Path $script:Work 'summary.json'))
    foreach ($item in $server.excluded) { Write-Host ('Separate/unconfigured: ' + $item) }
} catch {
    Write-Host ('STOP: ' + $_.Exception.Message)
    if ($null -ne $script:Work) { Write-Host ('Local diagnostics: ' + $script:Work) }
    exit 1
}
