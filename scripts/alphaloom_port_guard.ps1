param(
  [int]$Port = 8000,
  [string]$HostName = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$baseUrl = "http://${HostName}:$Port"

try {
  $openapi = Invoke-RestMethod -Uri "$baseUrl/openapi.json" -TimeoutSec 2
  $title = [string]$openapi.info.title
  if ($title -eq "AlphaLoom API") {
    Write-Output "ALPHALOOM_RUNNING $baseUrl"
    exit 0
  }
  if ([string]::IsNullOrWhiteSpace($title)) {
    $title = "unknown"
  }
  Write-Output "PORT_OCCUPIED_BY_OTHER $Port title=$title"
  exit 2
} catch {
  $probeError = $_.Exception.Message
}

try {
  $tcp = [System.Net.Sockets.TcpClient]::new()
  $task = $tcp.ConnectAsync($HostName, $Port)
  $listener = $task.Wait(500) -and $tcp.Connected
  $tcp.Close()
} catch {
  $listener = $false
}

if (-not $listener) {
  Write-Output "PORT_FREE $Port"
  exit 1
}

Write-Output "PORT_OCCUPIED_BY_OTHER $Port error=$probeError"
exit 2
