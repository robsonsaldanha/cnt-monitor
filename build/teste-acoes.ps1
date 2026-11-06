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

# ======================= v2.2 =======================
$r2 = [ordered]@{}
try { $ic = Invoke-WebRequest "$base/icone?exe=C:\Windows\System32\notepad.exe" -UseBasicParsing; $r2.icone = "HTTP $($ic.StatusCode) $($ic.Headers['Content-Type']) $($ic.RawContentLength) bytes" } catch { $r2.icone = "falhou: $_" }
$en = Invoke-RestMethod "$base/energia"
$r2.energia_planos = ($en.planos | ForEach-Object { "$($_.nome)$(if($_.ativo){'*'})" }) -join ', '
$r2.energia_ajustes = ($en.ajustes | ForEach-Object { "$($_.chave)=$($_.valor.tomada)/$($_.valor.bateria)" }) -join ' '
$r2.energia_modo = $en.modo.nome
$r2.energia_windows = ($en.windows | ForEach-Object { "$($_.chave)=$($_.ativo)" }) -join ' '
$orig = ($en.planos | Where-Object ativo).guid
$x = Post "acao" @{ tipo = "energia_criar"; base = "alto" }; $r2.criar_alto = "ok=$($x.ok) $($x.msg)"
$r2.plano_agora = ((Invoke-RestMethod "$base/energia/planos") | Where-Object ativo).nome
$x = Post "acao" @{ tipo = "energia_plano"; guid = $orig }; $r2.voltar_plano = "ok=$($x.ok)"
$tela = ($en.ajustes | Where-Object chave -eq 'tela').valor.tomada
$x = Post "acao" @{ tipo = "energia_ajuste"; chave = "tela"; onde = "tomada"; valor = 7 }
$lido = ((Invoke-RestMethod "$base/energia").ajustes | Where-Object chave -eq 'tela').valor.tomada
$r2.ajuste_tela = "ok=$($x.ok) gravou 7, leu $lido"
Post "acao" @{ tipo = "energia_ajuste"; chave = "tela"; onde = "tomada"; valor = $tela } | Out-Null
$x = Post "acao" @{ tipo = "windows_ajuste"; chave = "transparencia"; ativo = $false }; $r2.transparencia_off = "ok=$($x.ok) $($x.msg)"
Post "acao" @{ tipo = "windows_ajuste"; chave = "transparencia"; ativo = $true } | Out-Null
$sv = Invoke-RestMethod "$base/servicos"
$r2.servicos = "$($sv.Count) serviços, $(($sv | Where-Object { -not $_.protegido }).Count) de terceiros, rodando: $(($sv | Where-Object status -eq 'running').Count)"
$x = Post "acao" @{ tipo = "servico"; nome = "Spooler"; oque = "parar" }; $r2.servico_protegido = "ok=$($x.ok) $($x.msg)"
$terc = $sv | Where-Object { -not $_.protegido -and $_.status -eq 'stopped' -and $_.inicio -eq 'manual' } | Select-Object -First 1
if ($terc) { $x = Post "acao" @{ tipo = "servico"; nome = $terc.nome; oque = "iniciar" }; $r2.servico_terceiro = "$($terc.nome): ok=$($x.ok) $($x.msg)"; Post "acao" @{ tipo = "servico"; nome = $terc.nome; oque = "parar" } | Out-Null }
$cfg0 = (Invoke-RestMethod "$base/config").valores
Post "config" @{ alertas = @(@{ tipo = "mem"; limite = 1; duracao_s = 0; ativo = $true }) } | Out-Null
Start-Sleep 4
$al = Invoke-RestMethod "$base/alertas"
$r2.alerta_disparou = "historico=$($al.historico.Count) ultimo: $($al.historico[0].titulo) | $($al.historico[0].msg)"
Post "config" @{ alertas = $cfg0.alertas } | Out-Null
$x = Post "acao" @{ tipo = "alerta_teste" }; $r2.alerta_teste = "ok=$($x.ok) $($x.msg)"
$x = Post "acao" @{ tipo = "limpar_cache" }; $r2.limpar_cache = "ok=$($x.ok) $($x.msg)"
$x = Post "acao" @{ tipo = "jogo_ligar" }; $r2.jogo_ligar = "ok=$($x.ok) $($x.msg)"
Start-Sleep 5
$j = Invoke-RestMethod "$base/jogo"
$r2.jogo_estado = "ativo=$($j.ativo) presentmon=$($j.presentmon) fps_rodando=$($j.fps_rodando) erro=$($j.fps_erro) plano=$(((Invoke-RestMethod "$base/energia/planos") | Where-Object ativo).nome)"
$x = Post "acao" @{ tipo = "jogo_desligar" }; $r2.jogo_desligar = "ok=$($x.ok) $($x.msg) | plano=$(((Invoke-RestMethod "$base/energia/planos") | Where-Object ativo).nome)"
$x = Post "acao" @{ tipo = "atualizar_verificar" }; $r2.atualizacao = "ok=$($x.ok) $($x.msg)"
"--- v2.2 ---"
$r2.GetEnumerator() | ForEach-Object { "{0,-22} {1}" -f $_.Key, $_.Value }
$r2 | ConvertTo-Json | Out-File -Encoding utf8 api\teste-v22.json

# ============================== v2.3 ==============================
$r3 = [ordered]@{}
function Tenta($nome, [scriptblock]$b) { try { $r3[$nome] = & $b } catch { $r3[$nome] = "ERRO: $($_.Exception.Message)" } }
Tenta "api_v1_resumo" { $x = Invoke-RestMethod "$base/v1/resumo"; "ok=$($x.ok) cpu=$($x.dados.cpu) mem=$($x.dados.mem_pct)" }
foreach ($rec in "programas","processos","rede","conexoes","ips","hardware","temperaturas","discos","gpus","historico","lentidao","alertas","vazamentos","inicializacao","servicos","energia","jogo","vps","status") {
  Tenta "v1_$rec" { $x = Invoke-WebRequest "$base/v1/$rec" -UseBasicParsing; "$($x.StatusCode) $($x.Content.Length) bytes" }
}
Tenta "v1_post_recusado" { try { Invoke-RestMethod -Method Post "$base/v1/resumo" -Body "{}" -ContentType "application/json"; "ACEITOU (ruim)" } catch { "recusado (bom): $($_.Exception.Response.StatusCode.value__)" } }
Tenta "limpeza_analise" { $x = Invoke-RestMethod "$base/limpeza"; ($x.categorias | ForEach-Object { "$($_.id)=$([math]::Round($_.bytes/1MB))MB" }) -join " " }
Tenta "limpeza_temp" { $x = Post "acao" @{ tipo = "limpeza"; ids = @("temp_usuario","relatorios_erro") }; "ok=$($x.ok) $($x.msg)" }
Tenta "navegadores" { $x = Invoke-RestMethod "$base/navegadores"; ($x.navegadores | ForEach-Object { "$($_.nome)=$([math]::Round($_.bytes/1MB))MB aberto=$($_.aberto)" }) -join "; " }
Tenta "navegadores_limpar" { $x = Post "acao" @{ tipo = "navegadores"; ids = @("microsoftedge","googlechrome") }; "ok=$($x.ok) $($x.msg)" }
# regra: um Python "órfão" (pai já encerrado) vira o programa "Python" — nunca aplicar regra no PowerShell do teste
function PythonOrfao { Start-Process cmd -ArgumentList '/c','start','""','/min','python','-c','"import time; time.sleep(600)"' -WindowStyle Hidden; Start-Sleep 5
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*time.sleep(600)*' } | Sort-Object CreationDate -Descending | Select-Object -First 1 }
$po = PythonOrfao
$prog = if ($po) { ((Invoke-RestMethod "$base/v1/processos/$($po.ProcessId)").dados.programa) } else { "" }
$r3.regra_programa = "pid=$($po.ProcessId) programa=$prog"
if ($prog -and $prog -notmatch 'PowerShell|Terminal|Prompt|Runner|Sistema|Windows') {
  Tenta "regra_salvar" { $x = Post "acao" @{ tipo = "regra_salvar"; regra = @{ programa = $prog; prioridade = "baixa"; nucleos = @(0); avisar_ao_abrir = $true } }; "ok=$($x.ok) $($x.msg)" }
  Start-Sleep 6
  Tenta "regra_aplicada" { $pp = Get-Process -Id $po.ProcessId; "prioridade=$($pp.PriorityClass) (esperado Idle) afinidade=$($pp.ProcessorAffinity) (esperado 1)" }
  Tenta "regra_log" { ((Invoke-RestMethod "$base/regras").log | Select-Object -First 3 | ForEach-Object msg) -join " | " }
  $rid = ((Invoke-RestMethod "$base/regras").regras | Select-Object -First 1).id
  Post "acao" @{ tipo = "regra_remover"; id = $rid } | Out-Null
  Stop-Process -Id $po.ProcessId -Force -ErrorAction SilentlyContinue
  $po = PythonOrfao
  Post "acao" @{ tipo = "regra_salvar"; regra = @{ programa = $prog; bloquear = $true } } | Out-Null
  Start-Sleep 6
  Tenta "regra_bloquear" { "processo ainda existe: $([bool](Get-Process -Id $po.ProcessId -ErrorAction SilentlyContinue)) (esperado False)" }
  $rid = ((Invoke-RestMethod "$base/regras").regras | Select-Object -First 1).id
  Post "acao" @{ tipo = "regra_remover"; id = $rid } | Out-Null
} else { $r3.regra_salvar = "PULADO: programa '$prog' não é seguro para testar" }
# backup ida e volta
Tenta "backup" { $b = Invoke-RestMethod "$base/backup"; $b.config.limiar_mem = 77; $x = Invoke-RestMethod -Method Post "$base/backup/restaurar" -Headers $h -ContentType "application/json" -Body ($b | ConvertTo-Json -Depth 10 -Compress); $v = (Invoke-RestMethod "$base/config").valores.limiar_mem; Post "config" @{ limiar_mem = 88 } | Out-Null; "ok=$($x.ok) limiar depois=$v (esperado 77) | $($x.msg)" }
# VPS: validação e teste (porta fechada)
Tenta "vps_testar" { $x = Post "acao" @{ tipo = "vps_testar"; vps = @{ host = "127.0.0.1"; porta = 1 }; segredo = @{ senha = "x" } }; "ok=$($x.ok) $($x.msg)" }
Tenta "vps_salvar" { $x = Post "acao" @{ tipo = "vps_salvar"; vps = @{ nome = "teste"; host = "127.0.0.1"; porta = 1; servicos = @("nginx", "rm -rf /") }; segredo = @{ senha = "segredo" } }; $l = Invoke-RestMethod "$base/vps"; "ok=$($x.ok) servicos=$($l[0].servicos -join ',') (esperado só nginx) tem_senha=$($l[0].tem_senha) segredos.bin=$(Test-Path C:\ProgramData\MonitorPC\segredos.bin)" }
Tenta "vps_senha_no_config" { $c = Get-Content C:\ProgramData\MonitorPC\config.json -Raw; "senha aparece no config.json: $($c.Contains('segredo')) (esperado False)" }
$l = Invoke-RestMethod "$base/vps"; if ($l) { Post "acao" @{ tipo = "vps_remover"; id = $l[0].id } | Out-Null }
# acesso pela rede: senha, login, somente ver, chave da API
Tenta "acesso_senha" { $x = Post "acesso/senha" @{ senha = "teste123" }; "ok=$($x.ok)" }
Post "config" @{ acesso_remoto = $true; acesso_somente_ver = $true } | Out-Null
Start-Sleep 2
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254*" } | Select-Object -First 1).IPAddress
$rb = "http://$($ip):48732"
Tenta "remoto_sem_login" { $x = Invoke-WebRequest "$rb/api/agora" -UseBasicParsing -MaximumRedirection 0 -SkipHttpErrorCheck; "$($x.StatusCode) -> $($x.Headers.Location)" }
Tenta "remoto_senha_errada" { $x = Invoke-WebRequest "$rb/login" -Method Post -Body "senha=errada" -UseBasicParsing -SkipHttpErrorCheck; "$($x.StatusCode) (esperado 401)" }
Tenta "remoto_login" { $s = New-Object Microsoft.PowerShell.Commands.WebRequestSession; Invoke-WebRequest "$rb/login" -Method Post -Body "senha=teste123" -WebSession $s -UseBasicParsing | Out-Null; $x = Invoke-RestMethod "$rb/api/agora" -WebSession $s; $y = Invoke-WebRequest "$rb/api/acao" -Method Post -Body "{}" -ContentType "application/json" -WebSession $s -UseBasicParsing -SkipHttpErrorCheck; "agora cpu=$($x.cpu) | alterar pelo celular (somente ver): $($y.StatusCode) (esperado 403)" }
Tenta "api_chave" { $k = (Post "acesso/chave" @{}).chave; $x = Invoke-RestMethod "$rb/api/v1/resumo" -Headers @{ "X-API-Key" = $k }; $y = Invoke-WebRequest "$rb/api/v1/resumo" -UseBasicParsing -SkipHttpErrorCheck; "com chave ok=$($x.ok) | sem chave $($y.StatusCode) (esperado 401)" }
Tenta "firewall" { (Get-NetFirewallRule -DisplayName "Monitor do PC (acesso pelo celular)" -ErrorAction SilentlyContinue | ForEach-Object { "$($_.DisplayName) perfil=$($_.Profile)" }) -join "" }
Post "config" @{ acesso_remoto = $false } | Out-Null
Start-Sleep 1
Tenta "firewall_removido" { "regra existe: $([bool](Get-NetFirewallRule -DisplayName 'Monitor do PC (acesso pelo celular)' -ErrorAction SilentlyContinue)) (esperado False)" }
Tenta "fontes" { $x = Invoke-WebRequest "http://127.0.0.1:48731/fontes/barlow-latin-400-normal.woff2" -UseBasicParsing; "$($x.StatusCode) $($x.Headers['Content-Type'])" }
Tenta "navegar" { Post "ui/navegar" @{ aba = "otimizar" } | Out-Null; (Invoke-RestMethod "$base/agora").navegar.aba }
Tenta "icone_driverstore" { $e = Get-ChildItem C:\Windows\System32\DriverStore\FileRepository -Recurse -Filter *.exe -ErrorAction SilentlyContinue | Select-Object -First 3; ($e | ForEach-Object { $x = Invoke-WebRequest "$base/icone?exe=$([uri]::EscapeDataString($_.FullName))" -UseBasicParsing -SkipHttpErrorCheck; "$($_.Name)=$($x.StatusCode)" }) -join " " }
"--- v2.3 ---"
$r3.GetEnumerator() | ForEach-Object { "{0,-22} {1}" -f $_.Key, $_.Value }
$r3 | ConvertTo-Json | Out-File -Encoding utf8 api\teste-v23.json
