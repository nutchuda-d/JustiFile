# Calls the CGD procurement API with several filter combinations and saves the answers to cgd_probe.txt.
# Optional: -Dept <agency code>   e.g.  .\probe_cgd.ps1 -Dept 1234
param([string]$Dept = '')
$line = Get-Content .env | Where-Object { $_ -match '^CGD_API_KEY=' } | Select-Object -First 1
$k = ($line -replace '^CGD_API_KEY=', '').Trim().Trim('"')
if (-not $k) { Write-Host "CGD_API_KEY is empty in .env"; exit 1 }
$base = 'https://opend.data.go.th/govspending/service/egp-contract'

$tests = [ordered]@{
  'A year 2569'                        = 'year=2569'
  'B year 2570'                        = 'year=2570'
  'C 2569 budget_start=0 budget_end=500000' = 'year=2569&budget_start=0&budget_end=500000'
  'D 2569 budget_end=500000 only'      = 'year=2569&budget_end=500000'
  'E 2569 budget_start=1 budget_end=500000' = 'year=2569&budget_start=1&budget_end=500000'
}
if ($Dept) {
  $tests['F 2569 dept_code']             = "year=2569&dept_code=$Dept"
  $tests['G 2569 dept_code + budget']    = "year=2569&dept_code=$Dept&budget_start=0&budget_end=500000"
  $tests['H 2570 dept_code']             = "year=2570&dept_code=$Dept"
}

$out = @()
foreach ($name in $tests.Keys) {
  $full = $base + '?api-key=' + $k + '&limit=3&offset=0&' + $tests[$name]
  $status = 0; $bytes = $null
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $full -Headers @{ 'api-key' = $k } -TimeoutSec 60
    $status = [int]$r.StatusCode; $bytes = $r.RawContentStream.ToArray()
    $body = [System.Text.Encoding]::UTF8.GetString($bytes)
  } catch {
    $resp = $_.Exception.Response
    if ($resp) { $status = [int]$resp.StatusCode }
    $body = if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
  }
  $body = ([string]$body).Replace($k, '***')
  $total = ''
  if ($body -match '"total":\s*(\d+)') { $total = $Matches[1] }
  $out += "===== $name  HTTP $status  total=$total"
  $out += $body.Substring(0, [Math]::Min(3000, $body.Length))
  Write-Host ("{0,-42} HTTP {1}  total={2}" -f $name, $status, $total)
}
$out | Out-File -Encoding utf8 cgd_probe.txt
Write-Host "Saved cgd_probe.txt"
