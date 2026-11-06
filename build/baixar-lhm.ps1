# Baixa o LibreHardwareMonitorLib (e dependências) para .NET Framework e junta as DLLs em .\lhm
param([string]$Versao = "0.9.6")
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
$pkgs = Join-Path $raiz "build\pkgs"
$destino = Join-Path $raiz "lhm"
New-Item -ItemType Directory -Force -Path $pkgs, $destino | Out-Null

$nuget = (Get-Command nuget -ErrorAction SilentlyContinue).Source
if (-not $nuget) {
  $nuget = Join-Path $pkgs "nuget.exe"
  Invoke-WebRequest "https://dist.nuget.org/win-x86-commandline/latest/nuget.exe" -OutFile $nuget
}
& $nuget install LibreHardwareMonitorLib -Version $Versao -OutputDirectory $pkgs -Framework net472 -NonInteractive -DependencyVersion Lowest | Out-Host

$preferidos = @("net472","net471","net47","net462","net461","net46","net452","net451","net45","netstandard2.0","netstandard1.6","netstandard1.3","netstandard1.1","netstandard1.0")
foreach ($p in Get-ChildItem $pkgs -Directory) {
  $lib = Join-Path $p.FullName "lib"
  if (Test-Path $lib) {
    foreach ($tfm in $preferidos) {
      $d = Join-Path $lib $tfm
      if (Test-Path $d) {
        Copy-Item (Join-Path $d "*.dll") $destino -Force -ErrorAction SilentlyContinue
        Write-Host "  $($p.Name) -> $tfm"
        break
      }
    }
  }
  $nat = Join-Path $p.FullName "runtimes\win-x64\native"
  if (Test-Path $nat) { Copy-Item (Join-Path $nat "*") $destino -Force }
}
if (-not (Test-Path (Join-Path $destino "LibreHardwareMonitorLib.dll"))) {
  $achados = Get-ChildItem $pkgs -Recurse -Filter LibreHardwareMonitorLib.dll
  $achados | ForEach-Object { Write-Host "  encontrado: $($_.FullName)" }
  $pref = $achados | Where-Object { $_.FullName -match 'net4' } | Select-Object -First 1
  if (-not $pref) { $pref = $achados | Where-Object { $_.FullName -match 'netstandard' } | Select-Object -First 1 }
  if (-not $pref) { $pref = $achados | Select-Object -First 1 }
  if ($pref) { Copy-Item (Join-Path $pref.DirectoryName "*.dll") $destino -Force; Write-Host "  usando $($pref.DirectoryName)" }
}
Get-ChildItem $destino | Format-Table Name, Length | Out-Host
if (-not (Test-Path (Join-Path $destino "LibreHardwareMonitorLib.dll"))) { throw "LibreHardwareMonitorLib.dll não encontrado" }
