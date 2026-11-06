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
