# Testa ações do Monitor do PC instalado: encerrar processo, proteção do Windows,
# inicialização (ativar/desativar), ícone da bandeja (fila de ações) e janelas.
$ErrorActionPreference = "Continue"
$base = "http://127.0.0.1:48731/api"
$tok = (Get-Content C:\ProgramData\MonitorPC\token.txt -Raw).Trim()
$h = @{ "X-Token" = $tok }
function Post($c, $d) { Invoke-RestMethod -Method Post -Uri "$base/$c" -Headers $h -ContentType "application/json" -Body ($d | ConvertTo-Json -Compress) }
$res = [ordered]@{}

# 1) sem token deve ser recusado
try { Invoke-RestMethod -Method Post -Uri "$base/acao" -ContentType "application/json" -Body '{"tipo":"encerrar","pid":4}'; $res.sem_token = "ACEITOU (ruim)" } catch { $res.sem_token = "recusado (bom): $($_.Exception.Response.StatusCode.value__)" }

# 2) encerrar um processo comum
$p = Start-Process python -ArgumentList '-c','"import time; time.sleep(600)"' -PassThru -WindowStyle Hidden
Start-Sleep 3
$r = Post "acao" @{ tipo = "encerrar"; pid = $p.Id }
Start-Sleep 2
$res.encerrar = "$($r.msg) | ainda existe: $([bool](Get-Process -Id $p.Id -ErrorAction SilentlyContinue))"

# 3) proteção: lsass e explorer não podem ser encerrados
foreach ($n in "lsass","explorer","svchost") {
  $pp = Get-Process $n -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($pp) { $r = Post "acao" @{ tipo = "encerrar"; pid = $pp.Id }; $res["protecao_$n"] = "ok=$($r.ok) $($r.msg)" }
}

# 4) inicialização: cria item de teste, desativa, confere StartupApproved, reativa e remove
New-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MonitorPCTeste" -Value '"C:\Windows\System32\notepad.exe"' -Force | Out-Null
$lista = Invoke-RestMethod "$base/inicializacao"
$item = $lista | Where-Object { $_.nome -eq "MonitorPCTeste" }
$res.inicializacao_total = $lista.Count
if ($item) {
  $r = Post "acao" @{ tipo = "inicializacao"; id = $item.id; ativo = $false }
  $v = (Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" -Name "MonitorPCTeste" -ErrorAction SilentlyContinue).MonitorPCTeste
  $lista2 = Invoke-RestMethod "$base/inicializacao"
  $res.inicializacao_desativar = "$($r.msg) | byte0=$($v[0]) | ativo agora=$(($lista2 | Where-Object nome -eq 'MonitorPCTeste').ativo)"
  $r = Post "acao" @{ tipo = "inicializacao"; id = $item.id; ativo = $true }
  $lista3 = Invoke-RestMethod "$base/inicializacao"
  $res.inicializacao_ativar = "$($r.msg) | ativo agora=$(($lista3 | Where-Object nome -eq 'MonitorPCTeste').ativo)"
} else { $res.inicializacao_desativar = "item de teste não apareceu na lista" }
Remove-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "MonitorPCTeste" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKLM:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" -Name "MonitorPCTeste" -ErrorAction SilentlyContinue

# 5) ícone da bandeja + janelas: abre o Bloco de Notas e pede "mostrar janela"
$np = Start-Process notepad -PassThru
Start-Sleep 6
$st = Invoke-RestMethod "$base/status"
$res.bandeja_online = $st.bandeja_online
$r = Post "acao" @{ tipo = "mostrar"; pid = $np.Id }
$fim = $null
if ($r.id) { foreach ($i in 1..20) { Start-Sleep -Milliseconds 300; $x = Invoke-RestMethod "$base/acao/resultado?id=$($r.id)"; if ($x.resultado) { $fim = $x.resultado; break } } }
$res.mostrar_janela = if ($fim) { "ok=$($fim.ok) $($fim.msg)" } else { "sem resposta: $($r.msg)" }
$proc = Invoke-RestMethod "$base/processo?pid=$($np.Id)"
$res.janelas_notepad = "$($proc.janelas) janela(s): $($proc.lista_janelas -join ' | ') | protegido=$($proc.protegido)"
$r = Post "acao" @{ tipo = "pasta"; pid = $np.Id }
if ($r.id) { Start-Sleep 2; $x = Invoke-RestMethod "$base/acao/resultado?id=$($r.id)"; $res.abrir_pasta = "ok=$($x.resultado.ok) $($x.resultado.msg)" } else { $res.abrir_pasta = $r.msg }
Stop-Process -Id $np.Id -Force -ErrorAction SilentlyContinue

# 6) configurações
$c = Post "config" @{ intervalo_ms = 500 }
$res.config_intervalo = $c.valores.intervalo_ms
$c = Post "config" @{ intervalo_ms = 250 }
$run = (Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" -Name MonitorDoPC -ErrorAction SilentlyContinue).MonitorDoPC
$res.iniciar_com_windows = $run

$res.GetEnumerator() | ForEach-Object { "{0,-26} {1}" -f $_.Key, $_.Value }
$res | ConvertTo-Json | Out-File -Encoding utf8 api\teste-acoes.json
