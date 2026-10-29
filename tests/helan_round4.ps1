#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$SelfTest,
    [string]$RunnerRef = 'feature/hy2-jieshu-parity',
    [string]$SshHost = '162.211.231.219',
    [ValidateRange(1, 65535)][int]$SshPort = 22,
    [string]$SshKey = (Join-Path $env:USERPROFILE '.ssh\vps_root_ed25519')
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$CoreUrl = 'https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-windows-amd64.exe'
$CoreSha = '162ef8fe55dc7ec810dda662908f8e65ec36bd6da39cb87c3bd66184ab68f067'
$script:Client = $null
$serverProcess = $null
$echoServer = $null
$script:Work = $null
$script:Report = [ordered]@{ schema = 1; mode = 'public'; runner_ref = $RunnerRef; cases = @(); passed = 0; failed = 0; public_udp_client_tested = $false; real_wg_nat_tested = $false }
if ($SelfTest) { $script:Report.mode = 'windows-loopback' }

function Write-Utf8([string]$Path, [string]$Value) {
    [IO.File]::WriteAllText($Path, $Value, (New-Object Text.UTF8Encoding($false)))
}
function New-PrivateWork {
    $base = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    $path = Join-Path (Join-Path $base 'Hy2RealTest') ([guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $path -Force
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $aclArgs = @($path, '/inheritance:r', '/grant:r', "*${sid}:(OI)(CI)F", '*S-1-5-18:(OI)(CI)F')
    $null = & icacls.exe @aclArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict private test directory permissions' }
    return $path
}
function Write-Report {
    $script:Report.passed = @($script:Report.cases | Where-Object { $_.status -eq 'PASS' }).Count
    $script:Report.failed = @($script:Report.cases | Where-Object { $_.status -eq 'FAIL' }).Count
    Write-Utf8 (Join-Path $script:Work 'result.json') ($script:Report | ConvertTo-Json -Depth 10)
}
function Check([string]$Name, [scriptblock]$Action) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $row = [ordered]@{ case = $Name; status = 'PASS'; seconds = 0.0 }
    try { & $Action | Out-Null }
    catch { $row.status = 'FAIL'; $row.error = $_.Exception.Message; throw }
    finally {
        $row.seconds = [math]::Round($clock.Elapsed.TotalSeconds, 3)
        $script:Report.cases += $row
        Write-Report
        Write-Host ("{0}: {1} ({2}s)" -f $row.status, $Name, $row.seconds)
    }
}
function Free-TcpPort {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    try { $listener.Start(); return ([Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}
function Free-UdpPort {
    $udp = New-Object Net.Sockets.UdpClient(0)
    try { return ([Net.IPEndPoint]$udp.Client.LocalEndPoint).Port }
    finally { $udp.Close() }
}
function Start-Core([string]$Mode, [string]$ConfigPath, [string]$Name) {
    return Start-Process -FilePath (Join-Path $script:Work 'hysteria.exe') -PassThru -WindowStyle Hidden `
        -ArgumentList @('--log-level', 'debug', $Mode, '-c', ('"' + $ConfigPath + '"')) `
        -RedirectStandardOutput (Join-Path $script:Work ($Name + '.stdout.private.log')) `
        -RedirectStandardError (Join-Path $script:Work ($Name + '.stderr.private.log'))
}
function Read-CoreLog([string]$Name) {
    $text = ''
    foreach ($kind in @('stdout', 'stderr')) {
        $path = Join-Path $script:Work ($Name + '.' + $kind + '.private.log')
        if (Test-Path -LiteralPath $path) { $text += [string](Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue) }
    }
    return $text
}
function Wait-Core([Diagnostics.Process]$Process, [string]$Name, [string]$Message) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($clock.Elapsed.TotalSeconds -lt 25) {
        $Process.Refresh()
        if ($Process.HasExited) { throw "$Name exited before readiness; inspect its private log" }
        if ((Read-CoreLog $Name).Contains($Message)) { return }
        Start-Sleep -Milliseconds 100
    }
    throw "$Name did not become ready in 25 seconds; inspect its private log"
}
function Stop-OwnedProcess($Process) {
    if ($null -eq $Process) { return }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) { $Process.Kill(); $null = $Process.WaitForExit(5000) }
    } finally { $Process.Dispose() }
}
function Read-SshDescriptor {
    if ($RunnerRef -notmatch '^(?:[0-9a-f]{40}|feature/hy2-jieshu-parity)$') { throw 'Invalid runner reference' }
    if ($SshHost -ne '162.211.231.219') { throw 'This acceptance script is restricted to Helan' }
    if ($SshKey.Contains('"') -or -not (Test-Path -LiteralPath $SshKey -PathType Leaf)) { throw 'SSH identity file is unavailable' }
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    $remote = @'
set -Eeuo pipefail
[[ "$(hostname -s)" == helan && "$EUID" == 0 ]]
probe="$(mktemp /root/hy2-public-export.XXXXXX)"
trap 'rm -f -- "$probe"' EXIT
curl -fLsS --retry 2 --connect-timeout 10 --max-time 60 'https://raw.githubusercontent.com/liucong552-art/ceshi/__REF__/tests/helan_public_probe.py' -o "$probe"
printf 'HY2_PUBLIC_DESCRIPTOR_BEGIN\n'
PYTHONDONTWRITEBYTECODE=1 python3 -B "$probe"
printf '\nHY2_PUBLIC_DESCRIPTOR_END\n'
'@
    $remote = $remote.Replace('__REF__', $RunnerRef).Replace("`r`n", "`n") + "`n"
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $ssh
    $info.Arguments = '-i "' + $SshKey + '" -p ' + $SshPort + ' -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 root@' + $SshHost + ' bash -s'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $proc = New-Object Diagnostics.Process
    $proc.StartInfo = $info
    try {
        $null = $proc.Start()
        $outputTask = $proc.StandardOutput.ReadToEndAsync()
        $errorTask = $proc.StandardError.ReadToEndAsync()
        $proc.StandardInput.Write($remote)
        $proc.StandardInput.Close()
        if (-not $proc.WaitForExit(90000)) { $proc.Kill(); throw 'SSH export timed out' }
        $output = $outputTask.GetAwaiter().GetResult()
        Write-Utf8 (Join-Path $script:Work 'ssh.private.log') ($errorTask.GetAwaiter().GetResult())
        if ($proc.ExitCode -ne 0) { throw 'SSH read-only export failed; inspect ssh.private.log' }
        $match = [regex]::Match($output, '(?s)HY2_PUBLIC_DESCRIPTOR_BEGIN\r?\n(.*?)\r?\nHY2_PUBLIC_DESCRIPTOR_END')
        if (-not $match.Success) { throw 'SSH export did not return the expected private envelope' }
        try { return ($match.Groups[1].Value | ConvertFrom-Json) }
        catch { throw 'SSH returned an invalid credential descriptor' }
    } finally { $proc.Dispose() }
}
function Send-Udp([int]$Port, [byte[]]$Payload) {
    $udp = New-Object Net.Sockets.UdpClient
    try {
        $udp.Client.ReceiveTimeout = 6000
        $udp.Connect('127.0.0.1', $Port)
        $null = $udp.Send($Payload, $Payload.Length)
        $peer = New-Object Net.IPEndPoint([Net.IPAddress]::Any, 0)
        return ,($udp.Receive([ref]$peer))
    } finally { $udp.Close() }
}
function New-DnsQuery {
    $requestId = Get-Random -Minimum 1 -Maximum 65536
    $bytes = New-Object 'Collections.Generic.List[byte]'
    $bytes.Add([byte]($requestId -shr 8)); $bytes.Add([byte]($requestId -band 255))
    $bytes.AddRange([byte[]]@(1, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    foreach ($label in 'example.com'.Split('.')) {
        $part = [Text.Encoding]::ASCII.GetBytes($label)
        $bytes.Add([byte]$part.Length); $bytes.AddRange($part)
    }
    $bytes.AddRange([byte[]]@(0, 0, 1, 0, 1))
    return ,($bytes.ToArray())
}
function Assert-DnsReply([byte[]]$Query, [byte[]]$Reply) {
    if ($Reply.Length -lt 12 -or $Reply[0] -ne $Query[0] -or $Reply[1] -ne $Query[1] `
        -or ($Reply[2] -band 128) -eq 0 -or ($Reply[2] -band 2) -ne 0 `
        -or ($Reply[3] -band 15) -ne 0 -or (([int]$Reply[6] -shl 8) + $Reply[7]) -eq 0) {
        throw 'UDP DNS reply has wrong transaction ID, flags, error status or no answer'
    }
}
function Receive-Exact([IO.Stream]$Stream, [int]$Length) {
    $result = New-Object byte[] $Length
    $offset = 0
    while ($offset -lt $Length) {
        $count = $Stream.Read($result, $offset, $Length - $offset)
        if ($count -eq 0) { throw 'Unexpected EOF in SOCKS TCP probe' }
        $offset += $count
    }
    return ,$result
}
function Test-SocksEcho([int]$SocksPort, [int]$TargetPort) {
    $tcp = New-Object Net.Sockets.TcpClient
    try {
        $tcp.Connect('127.0.0.1', $SocksPort)
        $stream = $tcp.GetStream(); $stream.ReadTimeout = 6000; $stream.WriteTimeout = 6000
        $stream.Write([byte[]]@(5, 1, 0), 0, 3)
        $greeting = Receive-Exact $stream 2
        if ($greeting[0] -ne 5 -or $greeting[1] -ne 0) { throw 'SOCKS greeting failed' }
        $request = [byte[]]@(5, 1, 0, 1, 127, 0, 0, 1, ($TargetPort -shr 8), ($TargetPort -band 255))
        $stream.Write($request, 0, $request.Length)
        $reply = Receive-Exact $stream 4
        if ($reply[0] -ne 5 -or $reply[1] -ne 0) { throw 'SOCKS connect failed' }
        switch ($reply[3]) {
            1 { $null = Receive-Exact $stream 6 }
            4 { $null = Receive-Exact $stream 18 }
            3 { $size = (Receive-Exact $stream 1)[0]; $null = Receive-Exact $stream ($size + 2) }
            default { throw 'Invalid SOCKS reply address family' }
        }
        $data = [Text.Encoding]::ASCII.GetBytes(('Windows actual HY2 TCP echo' * 4096))
        $stream.Write($data, 0, $data.Length)
        $back = Receive-Exact $stream $data.Length
        if ([Convert]::ToBase64String($back) -ne [Convert]::ToBase64String($data)) { throw 'TCP echo mismatch' }
    } finally { $tcp.Close() }
}
function New-LoopbackFixture {
    # CI uses a trusted private CA only for this isolated Windows loopback case.
    if ($PSVersionTable.PSVersion.Major -lt 7) { throw 'The CI self-test needs PowerShell 7' }
    Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Sockets;
using System.Threading;
public sealed class Hy2ProbeEcho : IDisposable {
  TcpListener tcp; UdpClient udp; Thread t1, t2; volatile bool stop;
  public int TcpPort { get { return ((IPEndPoint)tcp.LocalEndpoint).Port; } }
  public int UdpPort { get { return ((IPEndPoint)udp.Client.LocalEndPoint).Port; } }
  public Hy2ProbeEcho() {
    tcp = new TcpListener(IPAddress.Loopback, 0); tcp.Start();
    udp = new UdpClient(new IPEndPoint(IPAddress.Loopback, 0));
    t1 = new Thread(() => { while (!stop) { try { using (var c = tcp.AcceptTcpClient()) {
      c.ReceiveTimeout = 6000; using (var s = c.GetStream()) { var b = new byte[65536]; int n;
        while ((n = s.Read(b,0,b.Length)) > 0) s.Write(b,0,n); }
    } } catch { if (stop) return; } } });
    t2 = new Thread(() => { while (!stop) { try { IPEndPoint p = new IPEndPoint(IPAddress.Any,0);
      var b = udp.Receive(ref p); udp.Send(b,b.Length,p); } catch { if (stop) return; } } });
    t1.IsBackground = true; t2.IsBackground = true; t1.Start(); t2.Start();
  }
  public void Dispose() { stop = true; tcp.Stop(); udp.Close(); t1.Join(1000); t2.Join(1000); }
}
'@
    $script:Echo = New-Object Hy2ProbeEcho
    $rsa = [Security.Cryptography.RSA]::Create(2048)
    try {
        $req = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=hy2.test', $rsa, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
        $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new(); $san.AddDnsName('hy2.test')
        $null = $req.CertificateExtensions.Add($san.Build())
        $null = $req.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($true, $false, 0, $true))
        $cert = $req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddDays(1))
        Write-Utf8 (Join-Path $script:Work 'ca.pem') $cert.ExportCertificatePem()
        Write-Utf8 (Join-Path $script:Work 'key.pem') $rsa.ExportPkcs8PrivateKeyPem()
        $cert.Dispose()
    } finally { $rsa.Dispose() }
    $script:TestServerPort = Free-UdpPort
    $config = @{ listen = ('127.0.0.1:' + $script:TestServerPort); tls = @{ cert = (Join-Path $script:Work 'ca.pem'); key = (Join-Path $script:Work 'key.pem') }; auth = @{ type = 'password'; password = 'windows-ci-local-probe' } }
    Write-Utf8 (Join-Path $script:Work 'server.private.json') ($config | ConvertTo-Json -Depth 8)
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Run this script in Windows PowerShell, outside SSH' }
    if (-not $SelfTest -and -not (Test-Path -LiteralPath $SshKey -PathType Leaf)) { throw 'The existing VPS SSH identity file was not found' }
    $script:Work = New-PrivateWork
    Write-Host ('Test directory: ' + $script:Work)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Check '01_official_Windows_core_SHA256' {
        Invoke-WebRequest -UseBasicParsing -Uri $CoreUrl -OutFile (Join-Path $script:Work 'hysteria.exe') -TimeoutSec 180
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $script:Work 'hysteria.exe')).Hash.ToLowerInvariant() -ne $CoreSha) { throw 'Official Windows core checksum mismatch' }
        $script:Report.binary_sha256 = $CoreSha
    }
    $script:SocksPort = Free-TcpPort
    $script:UdpPort = Free-UdpPort
    if ($SelfTest) {
        New-LoopbackFixture
        $echoServer = $script:Echo
        $serverProcess = Start-Core 'server' (Join-Path $script:Work 'server.private.json') 'server'
        Wait-Core $serverProcess 'server' 'server up and running'
        $script:Config = @{ server = ('127.0.0.1:' + $script:TestServerPort); auth = 'windows-ci-local-probe'; tls = @{ sni = 'hy2.test'; ca = (Join-Path $script:Work 'ca.pem') } }
        $remoteUdp = '127.0.0.1:' + $echoServer.UdpPort
    } else {
        $descriptor = Read-SshDescriptor
        if ($descriptor.schema -ne 1 -or [string]::IsNullOrWhiteSpace($descriptor.client.auth)) { throw 'Invalid SSH credential descriptor' }
        $script:Config = @{ server = [string]$descriptor.client.server; auth = [string]$descriptor.client.auth; tls = @{ sni = [string]$descriptor.client.tls.sni } }
        if ($null -ne $descriptor.client.PSObject.Properties['obfs']) { $script:Config.obfs = $descriptor.client.obfs }
        $script:Report.server = $script:Config.server
        $script:ExpectedExit = @([string]$descriptor.expected_exit_ip)
        if ($null -ne $descriptor.PSObject.Properties['expected_exit_ips']) {
            $script:ExpectedExit = @($descriptor.expected_exit_ips | ForEach-Object { [Net.IPAddress]::Parse([string]$_).ToString() })
        }
        $remoteUdp = '1.1.1.1:53'
    }
    $script:Config.socks5 = @{ listen = ('127.0.0.1:' + $script:SocksPort) }
    $script:Config.udpForwarding = @(@{ listen = ('127.0.0.1:' + $script:UdpPort); remote = $remoteUdp })
    $clientPath = Join-Path $script:Work 'client.private.json'
    Write-Utf8 $clientPath ($script:Config | ConvertTo-Json -Depth 12)
    Check '02_TLS_verified_and_HY2_authenticated' {
        $script:Client = Start-Core 'client' $clientPath 'client'
        Wait-Core $script:Client 'client' 'SOCKS5 server listening'
    }
    if ($SelfTest) {
        Check '03_actual_Windows_HY2_TCP_echo' { Test-SocksEcho $script:SocksPort $echoServer.TcpPort }
        Check '04_actual_Windows_HY2_UDP_echo_and_DNS_guard' {
            $data = [Text.Encoding]::ASCII.GetBytes('Windows actual HY2 UDP echo')
            $back = Send-Udp $script:UdpPort $data
            if ([Convert]::ToBase64String($back) -ne [Convert]::ToBase64String($data)) { throw 'UDP echo mismatch' }
            $query = New-DnsQuery
            $bad = [byte[]]$query.Clone()
            $rejected = $false
            try { Assert-DnsReply $query $bad } catch { $rejected = $true }
            if (-not $rejected) { throw 'DNS guard accepted a request as a reply' }
        }
    } else {
        Check '03_public_TCP_HTTPS_and_Helan_exit_IP' {
            $path = Join-Path $script:Work 'https-trace.txt'
            $curlArgs = @('--fail', '--silent', '--show-error', '--connect-timeout', '10', '--max-time', '30', '--socks5-hostname', ('127.0.0.1:' + $script:SocksPort), '--output', $path, 'https://www.cloudflare.com/cdn-cgi/trace')
            & curl.exe @curlArgs 2> (Join-Path $script:Work 'curl.private.log')
            if ($LASTEXITCODE -ne 0) { throw 'HTTPS through HY2 failed; inspect curl.private.log' }
            $trace = [IO.File]::ReadAllText($path)
            $match = [regex]::Match($trace, '(?m)^ip=([^\r\n]+)')
            if (-not $match.Success) { throw 'HTTPS trace has no proxy exit address' }
            $observed = [Net.IPAddress]::Parse($match.Groups[1].Value.Trim()).ToString()
            if ($script:ExpectedExit -notcontains $observed) { throw 'Proxy exit IP does not match Helan public addresses' }
            $script:Report.tcp_exit_ip = $observed
        }
        Check '04_public_UDP_DNS_transaction_and_answer' {
            $query = New-DnsQuery
            $reply = Send-Udp $script:UdpPort $query
            Assert-DnsReply $query $reply
            $script:Report.public_udp_client_tested = $true
        }
    }
    Write-Host 'PASS: 4/4. Real two-machine WG-NAT remains pending.'
} catch {
    if ($null -ne $script:Work) {
        $script:Report.error = $_.Exception.Message
        Write-Report
    }
    Write-Host ('STOP: ' + $_.Exception.Message)
    exit 1
} finally {
    Stop-OwnedProcess $script:Client
    Stop-OwnedProcess $serverProcess
    if ($null -ne $echoServer) { $echoServer.Dispose() }
    if ($null -ne $script:Work) {
        foreach ($name in @('client.private.json', 'server.private.json', 'key.pem', 'ca.pem')) {
            Remove-Item -LiteralPath (Join-Path $script:Work $name) -Force -ErrorAction SilentlyContinue
        }
        Write-Host ('Result: ' + (Join-Path $script:Work 'result.json'))
    }
}
