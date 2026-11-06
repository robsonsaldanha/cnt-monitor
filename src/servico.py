"""Serviço do Windows "Monitor do PC" (roda como administrador/SYSTEM).

  MonitorPCServico.exe                 -> chamado pelo Windows ao iniciar o serviço
  MonitorPCServico.exe --console       -> roda no terminal (teste)
  MonitorPCServico.exe --console --segundos 60 --saida teste.json
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitorpc import config  # noqa: E402

WIN = os.name == "nt"


def preparar_log():
    os.makedirs(config.BASE, exist_ok=True)
    try:
        if os.path.exists(config.LOG) and os.path.getsize(config.LOG) > 5 * 1024 * 1024:
            os.replace(config.LOG, config.LOG + ".1")
    except OSError:
        pass
    f = open(config.LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f
    print(f"\n=== Monitor do PC {config.VERSAO} iniciado em {time.strftime('%Y-%m-%d %H:%M:%S')} ===")


def iniciar_motor():
    from monitorpc import motor, servidor
    m = motor.Motor()
    m.iniciar()
    srv = servidor.criar(m)
    return m, srv


if WIN:
    import win32event
    import win32service
    import win32serviceutil
    import servicemanager

    class Servico(win32serviceutil.ServiceFramework):
        _svc_name_ = "MonitorPC"
        _svc_display_name_ = "Monitor do PC"
        _svc_description_ = ("Coleta uso de CPU, memória, disco, rede, conexões e temperatura por programa "
                             "para o Monitor do PC. Guarda 7 dias de histórico.")

        def __init__(self, args):
            super().__init__(args)
            self.ev = win32event.CreateEvent(None, 0, 0, None)
            self.motor = None
            self.srv = None

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            try:
                if self.motor:
                    self.motor.parar()
                if self.srv:
                    self.srv.shutdown()
            except Exception:
                pass
            win32event.SetEvent(self.ev)

        def SvcDoRun(self):
            preparar_log()
            try:
                self.motor, self.srv = iniciar_motor()
            except Exception:
                import traceback
                traceback.print_exc()
                raise
            win32event.WaitForSingleObject(self.ev, win32event.INFINITE)


def modo_console():
    segundos = None
    saida = None
    if "--segundos" in sys.argv:
        segundos = float(sys.argv[sys.argv.index("--segundos") + 1])
    if "--saida" in sys.argv:
        saida = sys.argv[sys.argv.index("--saida") + 1]
    m, srv = iniciar_motor()
    print(f"Monitor do PC rodando em {config.URL}  (Ctrl+C para sair)")
    ini = time.time()
    try:
        while segundos is None or time.time() - ini < segundos:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    if saida:
        rel = {"status": m.status(), "agora": m.agora(), "rede": m.rede_estado(), "hardware": m.hardware(),
               "n_processos": len(m.processos()), "processos": m.processos()[:400]}
        try:
            rel["inicializacao"] = m.inicializacao()
        except Exception as e:  # noqa: BLE001
            rel["inicializacao_erro"] = str(e)
        with open(saida, "w", encoding="utf-8") as f:
            json.dump(rel, f, ensure_ascii=False, indent=1, default=list)
    m.parar()
    srv.shutdown()


def main():
    if "--console" in sys.argv or not WIN:
        modo_console()
    elif len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(Servico)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(Servico)


if __name__ == "__main__":
    main()
