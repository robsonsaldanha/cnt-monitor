"""Limpeza segura de disco e de cache dos navegadores + otimização das unidades.

Regras de segurança:
- só apaga ARQUIVOS com mais de 1 dia (exceto Lixeira/cache de navegador fechado);
- arquivo em uso por algum programa é pulado (o Windows não deixa apagar);
- nunca segue atalhos/links simbólicos para fora da pasta;
- nada de registro, drivers, pastas de programas, documentos ou downloads;
- navegador aberto é pulado (pede para fechar).
"""
import os
import re
import time
import threading
import subprocess

import psutil

WIN = os.name == "nt"
SEM_JANELA = 0x08000000 if WIN else 0
WINDIR = os.environ.get("SystemRoot", r"C:\Windows")
PROGDATA = os.environ.get("ProgramData", r"C:\ProgramData")
UM_DIA = 86400


def _perfis():
    """[(usuario, pasta_perfil)] de todos os usuários do PC."""
    if not WIN:
        return [(os.environ.get("USER", "usuario"), os.path.expanduser("~"))]
    import winreg
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList") as k:
            i = 0
            while True:
                try:
                    sid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if not re.match(r"^S-1-5-21-[\d-]+$", sid):
                    continue
                try:
                    with winreg.OpenKey(k, sid) as k2:
                        p = os.path.expandvars(winreg.QueryValueEx(k2, "ProfileImagePath")[0])
                    if os.path.isdir(p):
                        out.append((os.path.basename(p), p))
                except OSError:
                    continue
    except OSError:
        pass
    return out


def _varrer(pasta, idade_min=UM_DIA, apagar=False, filtro=None):
    """Soma (ou apaga) arquivos dentro da pasta. Devolve (bytes, arquivos, pulados)."""
    total = n = pulados = 0
    if not os.path.isdir(pasta):
        return 0, 0, 0
    agora = time.time()
    for raiz, dirs, arqs in os.walk(pasta, topdown=False, followlinks=False):
        for a in arqs:
            c = os.path.join(raiz, a)
            if filtro and not filtro(c):
                continue
            try:
                st = os.lstat(c)
                if os.path.islink(c) or (idade_min and agora - st.st_mtime < idade_min):
                    continue
                if apagar:
                    os.remove(c)
                total += st.st_size
                n += 1
            except OSError:
                pulados += 1
        if apagar and raiz != pasta:
            try:
                if not os.listdir(raiz) and not os.path.islink(raiz):
                    os.rmdir(raiz)
            except OSError:
                pass
    return total, n, pulados


# ---------------------------------------------------------------- categorias de disco
def _pastas_temp_usuarios():
    return [os.path.join(p, "AppData", "Local", "Temp") for _u, p in _perfis()]


def _pastas_miniaturas():
    return [os.path.join(p, "AppData", "Local", "Microsoft", "Windows", "Explorer") for _u, p in _perfis()]


def _lixeiras():
    out = []
    for letra in "CDEFGHIJ":
        raiz = f"{letra}:\\$Recycle.Bin"
        if os.path.isdir(raiz):
            try:
                for sid in os.listdir(raiz):
                    if sid.startswith("S-1-5-21-"):
                        out.append(os.path.join(raiz, sid))
            except OSError:
                pass
    return out


CATEGORIAS = [
    {"id": "temp_usuario", "nome": "Arquivos temporários dos usuários",
     "desc": "A pasta Temp de cada usuário. Só arquivos com mais de 1 dia que nenhum programa está usando.",
     "pastas": _pastas_temp_usuarios, "idade": UM_DIA, "marcado": True},
    {"id": "temp_windows", "nome": "Arquivos temporários do Windows",
     "desc": r"C:\Windows\Temp. Só arquivos com mais de 1 dia e que não estejam em uso.",
     "pastas": lambda: [os.path.join(WINDIR, "Temp")], "idade": UM_DIA, "marcado": True},
    {"id": "relatorios_erro", "nome": "Relatórios de erro do Windows",
     "desc": "Relatórios de programas que travaram, já guardados/enviados. Não afetam nada.",
     "pastas": lambda: [os.path.join(PROGDATA, r"Microsoft\Windows\WER\ReportArchive"),
                        os.path.join(PROGDATA, r"Microsoft\Windows\WER\ReportQueue")], "idade": UM_DIA, "marcado": True},
    {"id": "despejos", "nome": "Despejos de memória de telas azuis",
     "desc": "Arquivos que só servem para um técnico investigar uma tela azul antiga.",
     "pastas": lambda: [os.path.join(WINDIR, "Minidump"), os.path.join(WINDIR, "LiveKernelReports")],
     "extras": lambda: [os.path.join(WINDIR, "MEMORY.DMP")], "idade": UM_DIA, "marcado": True},
    {"id": "entrega", "nome": "Cache de Otimização de Entrega",
     "desc": "Pedaços de atualizações do Windows guardados para compartilhar. O Windows baixa de novo se precisar.",
     "pastas": lambda: [os.path.join(WINDIR, r"ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Cache"),
                        os.path.join(WINDIR, "SoftwareDistribution", "DeliveryOptimization")], "idade": 0, "marcado": True},
    {"id": "miniaturas", "nome": "Cache de miniaturas",
     "desc": "As imagens pequenas que aparecem nas pastas. O Explorer recria sozinho quando você abrir a pasta.",
     "pastas": _pastas_miniaturas, "idade": 0, "marcado": False,
     "filtro": lambda c: os.path.basename(c).lower().startswith(("thumbcache_", "iconcache_"))},
    {"id": "lixeira", "nome": "Lixeira",
     "desc": "Esvazia a Lixeira de todos os usuários. Depois disso não dá para recuperar esses arquivos.",
     "pastas": _lixeiras, "idade": 0, "marcado": False,
     "filtro": lambda c: os.path.basename(c).lower() != "desktop.ini"},
]


def analisar():
    out = []
    for cat in CATEGORIAS:
        total = n = 0
        for p in cat["pastas"]():
            b, q, _ = _varrer(p, cat["idade"], False, cat.get("filtro"))
            total += b
            n += q
        for e in cat.get("extras", lambda: [])():
            try:
                if os.path.isfile(e) and time.time() - os.path.getmtime(e) > cat["idade"]:
                    total += os.path.getsize(e)
                    n += 1
            except OSError:
                pass
        out.append({"id": cat["id"], "nome": cat["nome"], "desc": cat["desc"], "bytes": total, "arquivos": n,
                    "marcado": cat["marcado"]})
    return out


def limpar(ids):
    res = []
    for cat in CATEGORIAS:
        if cat["id"] not in ids:
            continue
        total = n = pulados = 0
        if cat["id"] == "entrega" and WIN:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Delete-DeliveryOptimizationCache -Force -ErrorAction SilentlyContinue"],
                           capture_output=True, timeout=120, creationflags=SEM_JANELA)
        for p in cat["pastas"]():
            b, q, pu = _varrer(p, cat["idade"], True, cat.get("filtro"))
            total += b
            n += q
            pulados += pu
        for e in cat.get("extras", lambda: [])():
            try:
                if os.path.isfile(e) and time.time() - os.path.getmtime(e) > cat["idade"]:
                    tam = os.path.getsize(e)
                    os.remove(e)
                    total += tam
                    n += 1
            except OSError:
                pulados += 1
        res.append({"id": cat["id"], "nome": cat["nome"], "bytes": total, "arquivos": n, "pulados": pulados})
    return res


# ---------------------------------------------------------------- navegadores
NAVEGADORES = [
    # nome, exe, pasta base relativa a LOCALAPPDATA (Chromium) ou tipo firefox
    ("Google Chrome", "chrome.exe", r"Google\Chrome\User Data"),
    ("Microsoft Edge", "msedge.exe", r"Microsoft\Edge\User Data"),
    ("Brave", "brave.exe", r"BraveSoftware\Brave-Browser\User Data"),
    ("Vivaldi", "vivaldi.exe", r"Vivaldi\User Data"),
    ("Opera", "opera.exe", r"Opera Software\Opera Stable"),
    ("Opera GX", "opera.exe", r"Opera Software\Opera GX Stable"),
    ("Firefox", "firefox.exe", r"Mozilla\Firefox\Profiles"),
]
PASTAS_CACHE_CHROMIUM = [r"Cache\Cache_Data", "Cache", "Code Cache", "GPUCache", r"Service Worker\ScriptCache"]
AVISO_NAVEGADOR = ("Apaga só o CACHE: cópias de imagens, scripts e páginas que o navegador guarda para abrir sites "
                   "mais rápido. NÃO apaga senhas, histórico, favoritos, cookies (você continua logado nos sites), "
                   "extensões, downloads nem abas abertas. Depois da limpeza os sites podem demorar um pouco mais "
                   "para carregar na primeira visita. O navegador precisa estar fechado.")


def _pastas_navegador(base_local, tipo_firefox):
    """Pastas de cache de cada perfil do navegador."""
    out = []
    if not os.path.isdir(base_local):
        return out
    if tipo_firefox:
        for perfil in os.listdir(base_local):
            p = os.path.join(base_local, perfil, "cache2")
            if os.path.isdir(p):
                out.append(p)
        return out
    candidatos = [base_local]
    try:
        for d in os.listdir(base_local):
            if d == "Default" or d.startswith("Profile ") or d in ("Guest Profile", "System Profile"):
                candidatos.append(os.path.join(base_local, d))
    except OSError:
        pass
    for perfil in candidatos:
        for sub in PASTAS_CACHE_CHROMIUM:
            p = os.path.join(perfil, sub)
            if os.path.isdir(p) and p not in out and not any(p.startswith(x + os.sep) for x in out):
                out.append(p)
    return out


def _abertos():
    """Navegadores abertos (ignora a janela do próprio Monitor do PC, que usa o Edge)."""
    nomes = set()
    for p in psutil.process_iter(["name"]):
        n = (p.info.get("name") or "").lower()
        if not n:
            continue
        if n in ("msedge.exe", "chrome.exe"):
            try:
                cmd = " ".join(p.cmdline()).lower()
                if "monitorpc" in cmd and "user-data-dir" in cmd:
                    continue
            except (psutil.Error, OSError):
                pass
        nomes.add(n)
    return nomes


def navegadores(apagar_ids=None):
    """Analisa (ou limpa, se apagar_ids) o cache de cada navegador de cada usuário."""
    abertos = _abertos()
    out = []
    for nome, exe, rel in NAVEGADORES:
        ff = nome == "Firefox"
        pastas = []
        for _u, perfil in _perfis():
            pastas += _pastas_navegador(os.path.join(perfil, "AppData", "Local", rel), ff)
        if not pastas:
            continue
        nid = re.sub(r"[^a-z0-9]", "", nome.lower())
        aberto = exe.lower() in abertos
        total = n = pulados = 0
        apagar = bool(apagar_ids and nid in apagar_ids and not aberto)
        for p in pastas:
            b, q, pu = _varrer(p, 0, apagar)
            total += b
            n += q
            pulados += pu
        out.append({"id": nid, "nome": nome, "bytes": total, "arquivos": n, "aberto": aberto, "limpo": apagar,
                    "pulados": pulados, "perfis": len(pastas)})
    return out


# ---------------------------------------------------------------- tarefas longas (otimizar unidades, componentes)
class Tarefas:
    def __init__(self):
        self.atual = None
        self.historico = []
        self.lock = threading.Lock()

    def rodar(self, tipo):
        with self.lock:
            if self.atual and self.atual["situacao"] == "rodando":
                return False, f'Já tem uma tarefa rodando: {self.atual["nome"]}.'
            if tipo == "otimizar":
                nome = "Otimizar unidades (TRIM no SSD / desfragmentar HD)"
                cmd = ["defrag.exe", "/C", "/O", "/U", "/V"]
            elif tipo == "componentes":
                nome = "Limpeza de componentes do Windows (DISM)"
                cmd = ["Dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]
            else:
                return False, "Tarefa desconhecida."
            self.atual = {"tipo": tipo, "nome": nome, "situacao": "rodando", "inicio": time.time(), "saida": ""}
        threading.Thread(target=self._exec, args=(cmd,), daemon=True).start()
        return True, f"{nome}: começou. Pode levar alguns minutos; você pode continuar usando o PC."

    def _exec(self, cmd):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=3600, creationflags=SEM_JANELA)
            saida = (r.stdout or b"").decode("cp850", "replace")[-3000:]
            ok = r.returncode == 0
        except Exception as e:  # noqa: BLE001
            saida, ok = str(e), False
        with self.lock:
            self.atual.update(situacao="concluída" if ok else "erro", fim=time.time(), saida=saida)
            self.historico = ([dict(self.atual)] + self.historico)[:10]

    def estado(self):
        with self.lock:
            return {"atual": dict(self.atual) if self.atual else None, "historico": list(self.historico)}
