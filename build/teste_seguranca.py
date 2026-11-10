"""Testes das proteções de segurança (limpeza, ícones, VPS, acesso pela rede e atualização).

    python build/teste_seguranca.py
"""
import os
import sys
import json
import time
import shutil
import hashlib
import tempfile
import threading
import subprocess
import http.client
from http.server import ThreadingHTTPServer

DADOS = tempfile.mkdtemp(prefix="mpc-teste-seg-")
os.environ["MONITORPC_DADOS"] = DADOS   # antes de importar: nada toca na pasta de dados de verdade
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from monitorpc import atualizacao, config, configuracao, icones, limpeza, servidor, vps  # noqa: E402

WIN = os.name == "nt"


def teste_limpeza_nao_segue_juncao():
    """Uma junção dentro da pasta Temp não pode levar a limpeza (que roda como SYSTEM) para outra pasta."""
    raiz = tempfile.mkdtemp(prefix="mpc-limp-")
    try:
        fora, temp = os.path.join(raiz, "fora"), os.path.join(raiz, "temp")
        os.makedirs(os.path.join(temp, "sub", "vazia"))
        os.makedirs(fora)
        for c in (os.path.join(fora, "documento.txt"), os.path.join(temp, "lixo.tmp"), os.path.join(temp, "sub", "lixo2.tmp")):
            with open(c, "w") as f:
                f.write("x" * 10)
        elo = os.path.join(temp, "elo")
        if WIN:
            subprocess.run(["cmd", "/c", "mklink", "/J", elo, fora], check=True, capture_output=True)
        else:
            os.symlink(fora, elo)
        assert limpeza._eh_elo(elo) and not limpeza._eh_elo(fora)
        assert limpeza._varrer(temp, 0, False) == (20, 2, 0)          # só o que é da pasta
        assert limpeza._varrer(temp, 0, True) == (20, 2, 0)
        assert os.path.isfile(os.path.join(fora, "documento.txt"))    # o alvo da junção ficou intacto
        assert not os.path.exists(os.path.join(temp, "sub")) and os.path.lexists(elo)
        assert limpeza._varrer(elo, 0, True) == (0, 0, 0)             # nem quando a própria pasta é uma junção
        assert limpeza._varrer(temp, 86400, False)[1] == 0            # arquivo novo continua sendo poupado
    finally:
        if WIN and os.path.lexists(os.path.join(raiz, "temp", "elo")):
            os.rmdir(os.path.join(raiz, "temp", "elo"))
        shutil.rmtree(raiz, ignore_errors=True)


def teste_icone_so_disco_local():
    antes, icones.WIN = icones.WIN, True
    try:
        for c in (r"\\10.0.0.9\pasta\a.exe", r"\\?\C:\Windows\notepad.exe", "//servidor/pasta/a.exe", "a.exe", r"C:\a|b.exe"):
            assert icones.png(c) is None and c.lower() not in icones._mem, c
    finally:
        icones.WIN = antes


class _SSHFalso:
    """No lugar do paramiko: anota para onde iria cada senha, sem abrir conexão."""
    enviados = []

    class MissingHostKeyPolicy:
        pass

    class SSHException(Exception):
        pass

    class SSHClient:
        def set_missing_host_key_policy(self, p):
            pass

        def connect(self, **kw):
            _SSHFalso.enviados.append((kw["hostname"], kw["port"], kw.get("password"), kw.get("pkey")))
            raise OSError("sem rede no teste")

        def close(self):
            pass


def _motor_vps():
    from monitorpc import motor
    m = motor.Motor.__new__(motor.Motor)      # só o que as ações de VPS usam, sem ligar coletores
    m.lock, m.procs, m.estado = threading.Lock(), {}, {}
    m.vps = vps.Gerenciador()
    m.vps.sincronizar = lambda lista: None
    return m


def teste_vps_senha_presa_ao_endereco():
    """A senha guardada só vai para o endereço para o qual foi salva, por qualquer caminho."""
    ssh_antes, carregar_antes = sys.modules.get("paramiko"), vps.carregar_chave
    sys.modules["paramiko"], vps.carregar_chave = _SSHFalso, lambda texto, frase=None: "CHAVE:" + texto
    env = _SSHFalso.enviados
    base = {"id": "abc", "host": "meu.servidor", "porta": 22, "usuario": "root", "auth": "senha"}
    falso = dict(base, host="10.9.9.9")
    try:
        m = _motor_vps()
        assert m.acao({"tipo": "vps_salvar", "vps": base, "segredo": {"senha": "s3gredo"}})["ok"]
        assert vps.segredo_de(vps.validar([base])[0])["senha"] == "s3gredo"
        assert m.acao({"tipo": "vps_salvar", "vps": dict(base, nome="Outro nome")})["ok"]      # mesmo endereço: mantém
        assert m.vps.estado(configuracao.obter("vps"))[0]["tem_senha"]

        def recusado(r):
            return not r["ok"] and r["msg"] == vps.MSG_SEGREDO

        # endereço ou porta novos sem a senha digitada (ou só com a credencial do outro tipo de login)
        for troca, seg in ((falso, {}), (falso, None), (falso, {"chave": "qualquer"}), (falso, {"frase": "x"}),
                           (falso, {"limpar_chave": True}), (dict(base, porta=2222), {})):
            for tipo in ("vps_salvar", "vps_testar"):
                assert recusado(m.acao({"tipo": tipo, "vps": troca, "segredo": seg})), (tipo, troca, seg)
        assert recusado(m.acao({"tipo": "vps_testar", "vps": dict(falso, auth="chave"), "segredo": {"senha": "x"}}))
        assert not env and configuracao.obter("vps")[0]["host"] == "meu.servidor"

        m.acao({"tipo": "vps_testar", "vps": falso, "segredo": {"senha": "nova"}})      # digitou: vai a digitada
        assert env.pop() == ("10.9.9.9", 22, "nova", None) and not env
        m.acao({"tipo": "vps_testar", "vps": base})                                      # mesmo endereço: usa a guardada
        assert env.pop() == ("meu.servidor", 22, "s3gredo", None)

        # trocar o endereço por fora do formulário de VPS (configurações ou backup restaurado)
        for caminho in (lambda v: configuracao.salvar_publico({"vps": [v]}),
                        lambda v: configuracao.restaurar({"app": "monitor-pc", "config": {"vps": [v]}})):
            caminho(falso)
            cfg = configuracao.obter("vps")[0]
            assert cfg["host"] == "10.9.9.9" and vps.segredo_de(cfg) == {}
            assert not m.vps.estado([cfg])[0]["tem_senha"]
            s = vps.Servidor.__new__(vps.Servidor)
            s.cfg, s.ger, s.cliente = cfg, m.vps, None
            try:
                s._conectar()
                raise AssertionError("conectou com a senha de outro endereço")
            except ValueError as e:
                assert str(e) == vps.MSG_SEGREDO
            assert m.vps.testar(cfg, {})[0] is False and env.pop()[2] == "" and not env
            configuracao.salvar({"vps": [base]})
            assert vps.segredo_de(configuracao.obter("vps")[0])["senha"] == "s3gredo"      # voltou: continua valendo

        # salvar no endereço novo com outra senha descarta a antiga, e a chave antiga não acompanha
        assert m.acao({"tipo": "vps_salvar", "vps": base, "segredo": {"chave": "K", "frase": "f"}})["ok"]
        assert m.acao({"tipo": "vps_salvar", "vps": falso, "segredo": {"senha": "nova"}})["ok"]
        assert {k: v for k, v in vps.segredo("abc").items() if k != "destino"} == {"senha": "nova"}

        # senha guardada por versão antiga (sem endereço): fica presa ao servidor cadastrado na hora de iniciar
        vps._gravar_segredos({"abc": {"senha": "antiga"}})
        atual = configuracao.obter("vps")[0]
        assert vps.segredo_de(atual) == {}
        vps.vincular([atual])
        assert vps.segredo_de(atual)["senha"] == "antiga" and vps.segredo_de(dict(atual, host="outro")) == {}
        assert not env
    finally:
        vps.carregar_chave = carregar_antes
        if ssh_antes is None:
            sys.modules.pop("paramiko", None)
        else:
            sys.modules["paramiko"] = ssh_antes
        configuracao.salvar({"vps": []})


def teste_pasta_de_dados_protegida():
    """Depois de protegida, a pasta só dá leitura aos usuários comuns e não herda permissões de cima."""
    if not WIN:
        return
    import win32security
    import servico
    pasta = tempfile.mkdtemp(prefix="mpc-acl-")
    try:
        assert servico.proteger_pasta(pasta)
        sd = win32security.GetFileSecurity(pasta, win32security.DACL_SECURITY_INFORMATION)
        assert sd.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED      # sem herança
        dacl = sd.GetSecurityDescriptorDacl()
        escrita = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
        vistos = {}
        for i in range(dacl.GetAceCount()):
            ace = dacl.GetAce(i)
            assert ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE
            sid = win32security.ConvertSidToStringSid(ace[2])
            vistos[sid] = vistos.get(sid, 0) | ace[1]
        assert set(vistos) == {"S-1-5-18", "S-1-5-32-544", "S-1-5-32-545"}, vistos
        assert vistos["S-1-5-32-545"] & escrita == 0, hex(vistos["S-1-5-32-545"])
        assert vistos["S-1-5-32-545"] & 0x1 and vistos["S-1-5-18"] & 0x2                     # lê; o serviço grava
    finally:
        subprocess.run(["icacls", pasta, "/reset"], capture_output=True)
        shutil.rmtree(pasta, ignore_errors=True)


class MotorFalso:
    def __init__(self):
        self.recebidas = []

    def acao(self, a):
        self.recebidas.append(a)
        return {"ok": True}


def _post(porta, corpo, cabecalhos):
    con = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    con.request("POST", "/api/acao", json.dumps(corpo), {"Content-Type": "application/json", **cabecalhos})
    r = con.getresponse()
    r.read()
    con.close()
    return r.status


def teste_acoes_pela_rede():
    configuracao.salvar({"acesso_somente_ver": False})
    m = MotorFalso()
    rede = ThreadingHTTPServer(("127.0.0.1", 0), servidor.fabrica(m, "tok", remoto=True))
    local = ThreadingHTTPServer(("127.0.0.1", 0), servidor.fabrica(m, "tok"))
    for s in (rede, local):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servidor.SESSOES["sessao-teste"] = time.time() + 60
    cab = {"Cookie": "mpc_sessao=sessao-teste", "X-Token": "tok"}
    try:
        p = rede.server_address[1]
        for tipo in ("captura", "captura_enviar", "captura_apagar", "captura_tarefa", "vent_canal"):
            assert _post(p, {"tipo": tipo, "id": 1}, cab) == 403, tipo
        assert not m.recebidas
        assert _post(p, {"tipo": "abrir_exe", "caminho": r"\\10.0.0.9\x\virus.exe", "pid": 4}, cab) == 200
        assert m.recebidas.pop() == {"tipo": "abrir_exe", "pid": 4}     # o caminho mandado de fora é descartado
        assert _post(p, ["lista"], cab) == 400
        assert _post(p, {"tipo": "encerrar"}, {**cab, "X-Token": "errado"}) == 403
        assert _post(p, {"tipo": "encerrar"}, {**cab, "X-Token": "tók"}) == 403     # acento não derruba a conferência
        # no próprio PC nada muda
        cab_local = {"Host": f"127.0.0.1:{config.PORTA}", "X-Token": "tok"}
        assert _post(local.server_address[1], {"tipo": "abrir_exe", "caminho": r"C:\a.exe"}, cab_local) == 200
        assert m.recebidas.pop() == {"tipo": "abrir_exe", "caminho": r"C:\a.exe"}
        assert _post(local.server_address[1], {"tipo": "captura", "modo": "tela"}, cab_local) == 200
    finally:
        servidor.SESSOES.pop("sessao-teste", None)
        rede.shutdown()
        local.shutdown()


def teste_instalador_travado():
    """Entre conferir o SHA-256 e rodar o instalador ninguém pode trocar o arquivo."""
    if not WIN:
        return
    import pywintypes
    pasta = tempfile.mkdtemp(prefix="mpc-atu-")
    exe = os.path.join(pasta, "instalador.exe")
    try:
        shutil.copyfile(os.path.join(os.environ["SystemRoot"], "System32", "whoami.exe"), exe)
        with open(exe, "rb") as f:
            esperado = hashlib.sha256(f.read()).hexdigest()
        trava = atualizacao.travar(exe)
        try:
            assert atualizacao.sha256_travado(trava) == esperado
            for modo in ("ab", "r+b"):
                try:
                    open(exe, modo).close()
                    raise AssertionError("deu para escrever no arquivo travado")
                except PermissionError:
                    pass
            try:
                os.remove(exe)
                raise AssertionError("deu para apagar o arquivo travado")
            except PermissionError:
                pass
            assert subprocess.run([exe], capture_output=True, timeout=30).returncode == 0   # mas dá para rodar
        finally:
            trava.Close()
        escritor = open(exe, "ab")      # alguém com o arquivo aberto para escrita: a trava é recusada
        try:
            try:
                atualizacao.travar(exe).Close()
                raise AssertionError("travou com outro programa escrevendo")
            except pywintypes.error as e:
                assert e.winerror == 32   # ERROR_SHARING_VIOLATION
        finally:
            escritor.close()
    finally:
        shutil.rmtree(pasta, ignore_errors=True)

def teste_atualizacao_mostra_o_andamento():
    """Baixar e conferir informam a porcentagem; arquivo que não confere não é executado e o motivo fica no estado."""
    if not WIN:
        return
    import io
    conteudo = os.urandom(300_000)

    class Resp(io.BytesIO):
        headers = {"Content-Length": str(len(conteudo))}

    a = atualizacao.Atualizador()
    vistos, rodou = [], []
    andou = a._andou
    a._andou = lambda feito, total: (andou(feito, total), vistos.append((a.situacao, a.pct)))
    originais = atualizacao.urllib.request.urlopen, atualizacao.subprocess.Popen
    atualizacao.urllib.request.urlopen = lambda *x, **k: Resp(conteudo)
    atualizacao.subprocess.Popen = lambda cmd, **k: rodou.append(cmd)
    try:
        a.disponivel = {"versao": "9.9.9", "url": "https://exemplo.invalid/x.exe", "tamanho": len(conteudo),
                        "sha256": hashlib.sha256(conteudo).hexdigest()}
        ok, msg = a.instalar()
        assert ok and len(rodou) == 1 and rodou[0][0].endswith("MonitorDoPC-Setup-9.9.9.exe"), msg
        for etapa in ("baixando", "conferindo"):
            pcts = [p for s, p in vistos if s == etapa]
            assert len(pcts) >= 3 and pcts == sorted(pcts) and pcts[-1] == 100 and 0 < pcts[0] < 100, (etapa, pcts)
        e = a.estado()
        assert e["situacao"] == "instalando" and e["alvo"] == "9.9.9" and e["erro"] is None
        assert a.verificar() is a.disponivel and a.situacao == "instalando"      # não apaga o andamento
        assert a.instalar_ao_fundo() == (True, "A atualização já está em andamento.") and len(rodou) == 1

        b = atualizacao.Atualizador()
        b.disponivel = dict(a.disponivel, sha256="0" * 64)
        ok, msg = b.instalar_ao_fundo()
        assert ok and b.alvo == "9.9.9"
        for _ in range(100):
            if b.situacao == "erro":
                break
            time.sleep(0.05)
        assert b.situacao == "erro" and "não confere" in b.estado()["erro"] and len(rodou) == 1
    finally:
        atualizacao.urllib.request.urlopen, atualizacao.subprocess.Popen = originais
        shutil.rmtree(atualizacao.PASTA, ignore_errors=True)
        if os.path.exists(atualizacao.FLAG_REABRIR):
            os.remove(atualizacao.FLAG_REABRIR)


def teste_atualizacao_procura_de_10_em_10_minutos_sem_gastar_a_cota():
    """A procura é frequente, então manda o ETag: enquanto nada muda o GitHub responde 304, que não conta
    na cota de 60 consultas por hora, e o resultado continua sendo o da última resposta inteira."""
    import io
    assert atualizacao.VERIFICAR_CADA == 600
    corpo = json.dumps({"tag_name": "v99.0.0", "name": "x", "body": "SHA256: " + "ab" * 32, "html_url": "p",
                        "assets": [{"name": "MonitorDoPC-Setup-99.0.0.exe", "browser_download_url": "u", "size": 5}]}).encode()
    pedidos = []

    class Resp(io.BytesIO):
        headers = {"ETag": '"abc"'}

    def abrir(req, **k):
        pedidos.append(req.get_header("If-none-match"))
        if pedidos[-1] == '"abc"':
            raise atualizacao.urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        return Resp(corpo)

    original = atualizacao.urllib.request.urlopen
    atualizacao.urllib.request.urlopen = abrir
    try:
        a = atualizacao.Atualizador()
        primeira = a.verificar()
        assert primeira["versao"] == "99.0.0" and primeira["sha256"] == "ab" * 32
        a.disponivel, a.erro = None, "velho"
        assert a.verificar() == primeira and a.erro is None and a.situacao == "nova versão disponível"
        assert pedidos == [None, '"abc"']
    finally:
        atualizacao.urllib.request.urlopen = original


if __name__ == "__main__":
    try:
        for nome, f in list(globals().items()):
            if nome.startswith("teste_"):
                f()
                print("ok", nome)
        print("todos os testes de segurança passaram")
    finally:
        shutil.rmtree(DADOS, ignore_errors=True)
