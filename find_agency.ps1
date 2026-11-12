# Looks for an agency in the CGD procurement API and shows which name/code fields come back.
# Usage:  powershell -ExecutionPolicy Bypass -File .\find_agency.ps1
# Results are saved to find_agency.txt (the API key is masked).
param([string]$Name = 'เทศบาลตำบลศาลายา', [string]$Place = 'ศาลายา')
$line = Get-Content .env | Where-Object { $_ -match '^CGD_API_KEY=' } | Select-Object -First 1
$k = ($line -replace '^CGD_API_KEY=', '').Trim().Trim('"')
if (-not $k) { Write-Host "CGD_API_KEY is empty in .env"; exit 1 }
$base = 'https://opend.data.go.th/govspending/service/egp-contract'

function Ask($label, $query) {
  $url = $base + '?api-key=' + $k + '&year=2569&limit=100&offset=0&' + $query
  try {
    $r = Invoke-WebRequest -UseBasicParsing -Uri $url -Headers @{ 'api-key' = $k } -TimeoutSec 90
    $json = [System.Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray()) | ConvertFrom-Json
  } catch { return @("===== $label  ERROR: $($_.Exception.Message)") }
  $rows = @($json.data)
  $out = @("===== $label  total=$($json.total)  returned=$($rows.Count)")
  if ($rows.Count -gt 0) { $out += "fields: " + (($rows[0].PSObject.Properties | ForEach-Object { $_.Name }) -join ', ') }
  $rows | Group-Object { "$($_.dept_name) | $($_.dept_sub_name) | code=$($_.dept_code) | $($_.district) $($_.subdistrict)" } |
    Sort-Object Count -Descending | Select-Object -First 15 | ForEach-Object { $out += ("  {0,3}  {1}" -f $_.Count, $_.Name) }
  return $out
}

$all = @()
$all += Ask "keyword=$Name"  ("keyword=" + [uri]::EscapeDataString($Name))
$all += Ask "keyword=$Place" ("keyword=" + [uri]::EscapeDataString($Place))
$all | ForEach-Object { Write-Host $_ }
$all | Out-File -Encoding utf8 find_agency.txt
Write-Host "Saved find_agency.txt"
