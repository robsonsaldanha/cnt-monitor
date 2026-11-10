; Instalador do CNT Monitor (Inno Setup 6)
#ifndef Versao
  #define Versao "2.1.0"
#endif

[Setup]
AppId={{8E4C5B1A-2F7D-4E3A-9C61-5B0D7A3F2E91}
AppName=CNT Monitor
AppVersion={#Versao}
AppVerName=CNT Monitor {#Versao}
AppPublisher=Róbson Saldanha
DefaultDirName={autopf}\CNT Monitor
DefaultGroupName=CNT Monitor
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\saida
OutputBaseFilename=CNTMonitor-Setup-{#Versao}
SetupIconFile=icone.ico
UninstallDisplayIcon={app}\MonitorPC.exe
UninstallDisplayName=CNT Monitor
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktop"; Description: "Criar atalho na Área de Trabalho"
Name: "pawnio"; Description: "Instalar o driver PawnIO (necessário para ler a temperatura do processador)"

[Files]
Source: "..\dist\MonitorPC\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "..\dist\MonitorPCServico\*"; DestDir: "{app}\servico"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "..\dist\MonitorPCRemoto\*"; DestDir: "{app}\remoto"; Flags: recursesubdirs ignoreversion createallsubdirs
Source: "PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist; Tasks: pawnio

[Icons]
Name: "{autoprograms}\CNT Monitor"; Filename: "{app}\MonitorPC.exe"
Name: "{autodesktop}\CNT Monitor"; Filename: "{app}\MonitorPC.exe"; Tasks: desktop

[Registry]
; Botão direito do Explorer: "CNT Monitor" em qualquer arquivo e "CNT Monitor — imagem" nas imagens.
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC"; ValueType: string; ValueName: "MUIVerb"; ValueData: "CNT Monitor"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\MonitorPC.exe"
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC"; ValueType: string; ValueName: "SubCommands"; ValueData: ""
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC\shell\1enviar"; ValueType: string; ValueName: ""; ValueData: "Enviar e copiar o link"
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC\shell\1enviar\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta enviar ""%1"""
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC\shell\2copiar"; ValueType: string; ValueName: ""; ValueData: "Copiar o arquivo"
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC\shell\2copiar\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta copiar_arquivo ""%1"""
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC\shell\3hash"; ValueType: string; ValueName: ""; ValueData: "Verificar hash (MD5, SHA-256)"
Root: HKLM; Subkey: "Software\Classes\*\shell\MonitorPC\shell\3hash\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta hash ""%1"""
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem"; ValueType: string; ValueName: "MUIVerb"; ValueData: "CNT Monitor — imagem"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\MonitorPC.exe"
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem"; ValueType: string; ValueName: "SubCommands"; ValueData: ""
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\1editor"; ValueType: string; ValueName: ""; ValueData: "Editar no CNT Monitor"
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\1editor\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta editor ""%1"""
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\2fixar"; ValueType: string; ValueName: ""; ValueData: "Fixar na tela"
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\2fixar\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta fixar ""%1"""
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\3embelezar"; ValueType: string; ValueName: ""; ValueData: "Embelezar (cria uma cópia)"
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\3embelezar\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta embelezar ""%1"""
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\4ocr"; ValueType: string; ValueName: ""; ValueData: "Copiar o texto (OCR)"
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\4ocr\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta ocr ""%1"""
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\5qr"; ValueType: string; ValueName: ""; ValueData: "Ler código QR"
Root: HKLM; Subkey: "Software\Classes\SystemFileAssociations\image\shell\MonitorPCImagem\shell\5qr\command"; ValueType: string; ValueName: ""; ValueData: """{app}\MonitorPC.exe"" --ferramenta qr ""%1"""

[InstallDelete]
Type: files; Name: "{commonstartup}\Monitor do PC.lnk"
; atalhos com o nome antigo (antes de virar CNT Monitor)
Type: files; Name: "{autoprograms}\Monitor do PC.lnk"
Type: files; Name: "{autodesktop}\Monitor do PC.lnk"

[Run]
Filename: "{tmp}\PawnIO_setup.exe"; Parameters: "-install -silent"; StatusMsg: "Instalando o driver PawnIO..."; Flags: waituntilterminated skipifdoesntexist; Tasks: pawnio
Filename: "{sys}\sc.exe"; Parameters: "create MonitorPC binPath= ""\""{app}\servico\MonitorPCServico.exe\"""" start= auto DisplayName= ""CNT Monitor"""; StatusMsg: "Registrando o serviço..."; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "config MonitorPC binPath= ""\""{app}\servico\MonitorPCServico.exe\"""" start= auto DisplayName= ""CNT Monitor"""; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "description MonitorPC ""Coleta CPU, memória, disco, rede, IPs e temperatura por programa para o CNT Monitor."""; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "failure MonitorPC reset= 86400 actions= restart/5000/restart/10000/restart/60000"; Flags: runhidden waituntilterminated
; Acesso remoto na mesma rede: o técnico chega direto ao agente, e o firewall do Windows barraria essa entrada.
; Só UDP, só para o agente (que existe apenas durante um pedido de ajuda) e só vindo da rede local.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Monitor do PC (acesso remoto)"""; Flags: runhidden waituntilterminated
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""Monitor do PC (acesso remoto)"" dir=in action=allow program=""{app}\remoto\MonitorPCRemoto.exe"" protocol=UDP remoteip=localsubnet profile=any"; Flags: runhidden waituntilterminated
Filename: "{sys}\sc.exe"; Parameters: "start MonitorPC"; StatusMsg: "Iniciando o monitoramento..."; Flags: runhidden waituntilterminated
Filename: "{app}\MonitorPC.exe"; Description: "Abrir o CNT Monitor agora"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM MonitorPC.exe"; Flags: runhidden; RunOnceId: "fechaicone"
Filename: "{sys}\sc.exe"; Parameters: "stop MonitorPC"; Flags: runhidden waituntilterminated; RunOnceId: "paraservico"
Filename: "{sys}\timeout.exe"; Parameters: "/t 3 /nobreak"; Flags: runhidden waituntilterminated; RunOnceId: "espera"
Filename: "{sys}\reg.exe"; Parameters: "delete HKLM\Software\Microsoft\Windows\CurrentVersion\Run /v MonitorDoPC /f"; Flags: runhidden waituntilterminated; RunOnceId: "removeinicio"
Filename: "{sys}\sc.exe"; Parameters: "delete MonitorPC"; Flags: runhidden waituntilterminated; RunOnceId: "removeservico"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""Monitor do PC (acesso remoto)"""; Flags: runhidden waituntilterminated; RunOnceId: "removefirewall"

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\MonitorPC"
Type: filesandordirs; Name: "{app}"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  R: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MonitorPC.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
  Exec(ExpandConstant('{sys}\sc.exe'), 'stop MonitorPC', '', SW_HIDE, ewWaitUntilTerminated, R);
  Sleep(3000);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MonitorPCServico.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM MonitorPCRemoto.exe', '', SW_HIDE, ewWaitUntilTerminated, R);
  Result := '';
end;
