"""Histórico em SQLite: gravação, limpeza de 7 dias e consultas para as telas."""
import os
import time
import sqlite3

from . import config, configuracao

OUTROS = "Windows e outros (não atribuído)"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS geral(ts INTEGER, cpu REAL, mem_pct REAL, mem_usada INTEGER, mem_total INTEGER,
    swap_pct REAL, ler REAL, gravar REAL, env REAL, rec REAL, gpu REAL, temp_cpu REAL, temp_gpu REAL, temp_disco REAL);
CREATE TABLE IF NOT EXISTS apps(ts INTEGER, nome TEXT, mem INTEGER, cpu REAL, ler REAL, gravar REAL,
    env REAL, rec REAL, nproc INTEGER, nconn INTEGER);
CREATE TABLE IF NOT EXISTS procs(ts INTEGER, pid INTEGER, nome TEXT, programa TEXT, mem INTEGER, cpu REAL,
    ler REAL, gravar REAL, env REAL, rec REAL);
CREATE TABLE IF NOT EXISTS temps(ts INTEGER, id TEXT, hw TEXT, nome TEXT, valor REAL);
CREATE TABLE IF NOT EXISTS remotos(hora INTEGER, programa TEXT, ip TEXT, host TEXT, env INTEGER, rec INTEGER,
    PRIMARY KEY(hora, programa, ip));
CREATE TABLE IF NOT EXISTS alertas(ts INTEGER, regra TEXT, titulo TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS capturas(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, tipo TEXT, modo TEXT,
    caminho TEXT, largura INTEGER, altura INTEGER, bytes INTEGER, duracao REAL, url TEXT, mini BLOB);
CREATE INDEX IF NOT EXISTS i_geral ON geral(ts);
CREATE INDEX IF NOT EXISTS i_apps ON apps(ts);
CREATE INDEX IF NOT EXISTS i_apps_nome ON apps(nome, ts);
CREATE INDEX IF NOT EXISTS i_procs ON procs(ts);
CREATE INDEX IF NOT EXISTS i_temps ON temps(ts);
CREATE INDEX IF NOT EXISTS i_remotos_prog ON remotos(programa, hora);
"""


def conectar():
    con = sqlite3.connect(config.DB, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def iniciar():
    os.makedirs(config.BASE, exist_ok=True)
    novo = not os.path.exists(config.DB)
    con = sqlite3.connect(config.DB, timeout=15)
    if novo:
        con.execute("PRAGMA auto_vacuum=INCREMENTAL")
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(ESQUEMA)
    for col in ("favorito INTEGER DEFAULT 0", "tags TEXT DEFAULT ''"):   # colunas novas (2.4.0) em bancos antigos
        try:
            con.execute(f"ALTER TABLE capturas ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


def limpar(dias=7):
    lim = int(time.time() - dias * 86400)
    con = conectar()
    for t in ("geral", "apps", "procs", "temps", "alertas"):
        con.execute(f"DELETE FROM {t} WHERE ts < ?", (lim,))
    con.execute("DELETE FROM remotos WHERE hora < ?", (lim - 3600,))
    con.commit()
    con.execute("PRAGMA incremental_vacuum")
    con.commit()
    con.close()


def gravar(geral=None, apps=None, procs=None, temps=None, remotos=None):
    con = conectar()
    if geral:
        con.execute("INSERT INTO geral VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", geral)
    if apps:
        con.executemany("INSERT INTO apps VALUES (?,?,?,?,?,?,?,?,?,?)", apps)
    if procs:
        con.executemany("INSERT INTO procs VALUES (?,?,?,?,?,?,?,?,?,?)", procs)
    if temps:
        con.executemany("INSERT INTO temps VALUES (?,?,?,?,?)", temps)
    if remotos:
        con.executemany(
            "INSERT INTO remotos VALUES (?,?,?,?,?,?) ON CONFLICT(hora, programa, ip) DO UPDATE SET "
            "env = env + excluded.env, rec = rec + excluded.rec, host = COALESCE(excluded.host, host)", remotos)
    con.commit()
    con.close()


def _passo(segundos, pontos=360):
    return max(config.GRAVAR_CADA, int(segundos / pontos))


# ---------------------------------------------------------------- consultas
def historico(horas, programa=None):
    ate = int(time.time())
    desde = ate - int(horas * 3600)
    p = _passo(ate - desde)
    con = conectar()
    out = {"desde": desde, "ate": ate, "passo": p}
    if programa:
        out["programa"] = programa
        out["serie"] = con.execute(
            "SELECT (ts/?)*? b, AVG(mem), AVG(cpu), AVG(ler+gravar), AVG(env+rec), MAX(nproc), MAX(nconn) "
            "FROM apps WHERE nome=? AND ts>=? GROUP BY b ORDER BY b", (p, p, programa, desde)).fetchall()
    else:
        out["geral"] = con.execute(
            "SELECT (ts/?)*? b, AVG(cpu), MAX(mem_pct), AVG(ler), AVG(gravar), AVG(env), AVG(rec), AVG(gpu), "
            "MAX(temp_cpu), MAX(temp_gpu), MAX(temp_disco) FROM geral WHERE ts>=? GROUP BY b ORDER BY b",
            (p, p, desde)).fetchall()
        top = [r[0] for r in con.execute(
            "SELECT nome FROM apps WHERE ts>=? AND nome!=? GROUP BY nome ORDER BY SUM(mem) DESC LIMIT 6",
            (desde, OUTROS)).fetchall()]
        out["apps"] = {n: con.execute(
            "SELECT (ts/?)*? b, AVG(mem), AVG(cpu) FROM apps WHERE ts>=? AND nome=? GROUP BY b ORDER BY b",
            (p, p, desde, n)).fetchall() for n in top}
        topc = [r[0] for r in con.execute(
            "SELECT nome FROM apps WHERE ts>=? AND nome!=? GROUP BY nome ORDER BY SUM(cpu) DESC LIMIT 6",
            (desde, OUTROS)).fetchall()]
        out["apps_cpu"] = {n: con.execute(
            "SELECT (ts/?)*? b, AVG(cpu) FROM apps WHERE ts>=? AND nome=? GROUP BY b ORDER BY b",
            (p, p, desde, n)).fetchall() for n in topc}
    con.close()
    return out


def historico_temps(horas):
    ate = int(time.time())
    desde = ate - int(horas * 3600)
    p = _passo(ate - desde, 300)
    con = conectar()
    ids = con.execute("SELECT id, MAX(hw), MAX(nome), MAX(valor) m FROM temps WHERE ts>=? GROUP BY id "
                      "ORDER BY m DESC LIMIT 8", (desde,)).fetchall()
    series = []
    for sid, hw, nome, _m in ids:
        pts = con.execute("SELECT (ts/?)*? b, AVG(valor) FROM temps WHERE ts>=? AND id=? GROUP BY b ORDER BY b",
                          (p, p, desde, sid)).fetchall()
        series.append({"id": sid, "nome": f"{nome} · {hw}", "pts": pts})
    con.close()
    return {"desde": desde, "ate": ate, "passo": p, "series": series}


def momento(ts):
    con = conectar()
    r = con.execute("SELECT ts, cpu, mem_pct, mem_usada, mem_total, temp_cpu, temp_gpu FROM geral "
                    "ORDER BY ABS(ts-?) LIMIT 1", (ts,)).fetchone()
    if not r:
        con.close()
        return None
    apps = con.execute("SELECT nome, mem, cpu, ler+gravar, env+rec, nproc FROM apps WHERE ts=? ORDER BY mem DESC",
                       (r[0],)).fetchall()
    procs = con.execute("SELECT nome, programa, pid, mem, cpu FROM procs WHERE ts BETWEEN ? AND ? "
                        "ORDER BY mem DESC LIMIT 15", (r[0] - 60, r[0] + 60)).fetchall()
    con.close()
    return {"ts": r[0], "cpu": r[1], "mem_pct": r[2], "mem_usada": r[3], "mem_total": r[4],
            "temp_cpu": r[5], "temp_gpu": r[6],
            "apps": [{"nome": a[0], "mem": a[1], "cpu": a[2], "disco": a[3], "rede": a[4], "nproc": a[5]} for a in apps],
            "procs": [{"nome": a[0], "programa": a[1], "pid": a[2], "mem": a[3], "cpu": a[4]} for a in procs]}


def lentidao(desde, ate=None):
    ate = ate or int(time.time())
    con = conectar()
    linhas = con.execute(
        "SELECT ts, cpu, mem_pct, temp_cpu FROM geral WHERE ts BETWEEN ? AND ? AND (mem_pct>=? OR cpu>=? OR temp_cpu>=?) ORDER BY ts",
        (desde, ate, configuracao.obter("limiar_mem"), configuracao.obter("limiar_cpu"), configuracao.obter("limiar_temp"))).fetchall()
    eps = []
    for ts, cpu, mem, tc in linhas:
        if eps and ts - eps[-1]["fim"] <= 60:
            e = eps[-1]
            e["fim"] = ts
            if mem > e["mem_max"] or (mem == e["mem_max"] and cpu > e["cpu_max"]):
                e["ts_pico"] = ts
            e["mem_max"] = max(e["mem_max"], mem)
            e["cpu_max"] = max(e["cpu_max"], cpu)
            if tc is not None:
                e["temp_max"] = max(e["temp_max"] or 0, tc)
        else:
            eps.append({"inicio": ts - config.GRAVAR_CADA, "fim": ts, "mem_max": mem, "cpu_max": cpu,
                        "temp_max": tc, "ts_pico": ts})
    eps = eps[::-1][:150]
    for e in eps:
        e["top"] = [{"nome": a[0], "mem": a[1], "cpu": a[2]} for a in con.execute(
            "SELECT nome, mem, cpu FROM apps WHERE ts=? AND nome!=? ORDER BY mem DESC LIMIT 4",
            (e["ts_pico"], OUTROS)).fetchall()]
        e["top_cpu"] = [{"nome": a[0], "cpu": a[1]} for a in con.execute(
            "SELECT nome, cpu FROM apps WHERE ts=? AND nome!=? AND cpu>=5 ORDER BY cpu DESC LIMIT 3",
            (e["ts_pico"], OUTROS)).fetchall()]
    con.close()
    return eps


def remotos_programa(nome, horas=24 * 7):
    desde = int(time.time() - horas * 3600)
    con = conectar()
    r = con.execute("SELECT ip, MAX(host), SUM(env), SUM(rec), MIN(hora), MAX(hora) FROM remotos "
                    "WHERE programa=? AND hora>=? GROUP BY ip ORDER BY SUM(env+rec) DESC LIMIT 200",
                    (nome, desde)).fetchall()
    con.close()
    return [{"ip": a[0], "host": a[1], "env": a[2], "rec": a[3], "primeiro": a[4], "ultimo": a[5] + 3600} for a in r]


def relatorio(desde, ate):
    G = config.GRAVAR_CADA
    P = config.GRAVAR_PROCS_CADA
    con = conectar()
    g = con.execute(
        "SELECT COUNT(*), AVG(cpu), MAX(cpu), AVG(mem_pct), MAX(mem_pct), MAX(mem_usada), MAX(mem_total), "
        "SUM(ler), SUM(gravar), SUM(env), SUM(rec), AVG(gpu), MAX(gpu), AVG(temp_cpu), MAX(temp_cpu), "
        "AVG(temp_gpu), MAX(temp_gpu), MAX(temp_disco), SUM(mem_pct>=?), SUM(cpu>=?), MIN(ts), MAX(ts) "
        "FROM geral WHERE ts BETWEEN ? AND ?", (configuracao.obter("limiar_mem"), configuracao.obter("limiar_cpu"), desde, ate)).fetchone()
    geral = None
    if g and g[0]:
        geral = {"amostras": g[0], "tempo_monitorado": g[0] * G, "cpu_med": g[1], "cpu_max": g[2],
                 "mem_med": g[3], "mem_max": g[4], "mem_usada_max": g[5], "mem_total": g[6],
                 "ler": (g[7] or 0) * G, "gravar": (g[8] or 0) * G, "env": (g[9] or 0) * G, "rec": (g[10] or 0) * G,
                 "gpu_med": g[11], "gpu_max": g[12], "temp_cpu_med": g[13], "temp_cpu_max": g[14],
                 "temp_gpu_med": g[15], "temp_gpu_max": g[16], "temp_disco_max": g[17],
                 "tempo_mem_alta": (g[18] or 0) * G, "tempo_cpu_alta": (g[19] or 0) * G,
                 "primeiro": g[20], "ultimo": g[21]}
    progs = [{"nome": a[0], "tempo": a[1] * G, "mem_med": a[2], "mem_max": a[3], "cpu_med": a[4], "cpu_max": a[5],
              "ler": (a[6] or 0) * G, "gravar": (a[7] or 0) * G, "env": (a[8] or 0) * G, "rec": (a[9] or 0) * G,
              "nproc_max": a[10], "nconn_max": a[11]} for a in con.execute(
        "SELECT nome, COUNT(*), AVG(mem), MAX(mem), AVG(cpu), MAX(cpu), SUM(ler), SUM(gravar), SUM(env), SUM(rec), "
        "MAX(nproc), MAX(nconn) FROM apps WHERE ts BETWEEN ? AND ? AND nome!=? GROUP BY nome ORDER BY AVG(mem) DESC",
        (desde, ate, OUTROS)).fetchall()]
    procs = [{"nome": a[0], "programa": a[1], "mem_max": a[2], "cpu_med": a[3], "cpu_max": a[4],
              "disco": (a[5] or 0) * P, "rede": (a[6] or 0) * P, "instancias": a[7]} for a in con.execute(
        "SELECT nome, programa, MAX(mem), AVG(cpu), MAX(cpu), SUM(ler+gravar), SUM(env+rec), COUNT(DISTINCT pid) "
        "FROM procs WHERE ts BETWEEN ? AND ? GROUP BY nome, programa ORDER BY MAX(mem) DESC LIMIT 50",
        (desde, ate)).fetchall()]
    rem = [{"ip": a[0], "host": a[1], "programas": a[2], "env": a[3], "rec": a[4]} for a in con.execute(
        "SELECT ip, MAX(host), GROUP_CONCAT(DISTINCT programa), SUM(env), SUM(rec) FROM remotos "
        "WHERE hora BETWEEN ? AND ? GROUP BY ip ORDER BY SUM(env+rec) DESC LIMIT 50",
        ((desde // 3600) * 3600, ate)).fetchall()]
    temps = [{"hw": a[0], "nome": a[1], "min": a[2], "med": a[3], "max": a[4]} for a in con.execute(
        "SELECT MAX(hw), MAX(nome), MIN(valor), AVG(valor), MAX(valor) FROM temps WHERE ts BETWEEN ? AND ? "
        "GROUP BY id ORDER BY MAX(valor) DESC LIMIT 30", (desde, ate)).fetchall()]
    passo = 3600 if ate - desde > 36 * 3600 else 900
    linha = con.execute(
        "SELECT (ts/?)*? b, AVG(mem_pct), MAX(mem_pct), AVG(cpu), MAX(cpu), MAX(temp_cpu), MAX(temp_gpu) "
        "FROM geral WHERE ts BETWEEN ? AND ? GROUP BY b ORDER BY b", (passo, passo, desde, ate)).fetchall()
    con.close()
    return {"desde": desde, "ate": ate, "geral": geral, "programas": progs, "processos": procs, "remotos": rem,
            "temperaturas": temps, "linha": linha, "passo_linha": passo,
            "episodios": lentidao(desde, ate), "limiar_mem": configuracao.obter("limiar_mem"),
            "limiar_cpu": configuracao.obter("limiar_cpu"), "limiar_temp": configuracao.obter("limiar_temp")}


def gravar_alerta(ts, regra, titulo, msg):
    con = conectar()
    con.execute("INSERT INTO alertas VALUES (?,?,?,?)", (int(ts), regra, titulo, msg))
    con.commit()
    con.close()


def listar_alertas(limite=200):
    con = conectar()
    r = con.execute("SELECT ts, regra, titulo, msg FROM alertas ORDER BY ts DESC LIMIT ?", (limite,)).fetchall()
    con.close()
    return [{"ts": a[0], "id": a[1], "titulo": a[2], "msg": a[3]} for a in r]


def apagar_tudo():
    con = conectar()
    for t in ("geral", "apps", "procs", "temps", "remotos", "alertas"):
        con.execute(f"DELETE FROM {t}")
    con.commit()
    con.execute("PRAGMA incremental_vacuum")
    con.commit()
    con.close()


def info():
    con = conectar()
    r = con.execute("SELECT COUNT(*), MIN(ts) FROM geral").fetchone()
    con.close()
    tam = 0
    for suf in ("", "-wal"):
        try:
            tam += os.path.getsize(config.DB + suf)
        except OSError:
            pass
    return {"registros": r[0], "desde": r[1], "tamanho": tam}


# ---------------------------------------------------------------- capturas de tela (histórico)
def _sem_mini(r):
    return {"id": r[0], "ts": r[1], "tipo": r[2], "modo": r[3], "caminho": r[4], "largura": r[5], "altura": r[6],
            "bytes": r[7], "duracao": r[8], "url": r[9], "tem_mini": bool(r[10]), "favorito": bool(r[11]),
            "tags": r[12] or ""}


_CAMPOS_CAP = ("id, ts, tipo, modo, caminho, largura, altura, bytes, duracao, url, mini IS NOT NULL, "
               "COALESCE(favorito, 0), COALESCE(tags, '')")
MAX_CAPTURAS = 2000


def captura_registrar(d, mini=None):
    con = conectar()
    cur = con.execute("INSERT INTO capturas(ts, tipo, modo, caminho, largura, altura, bytes, duracao, url, mini) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (int(time.time()), d["tipo"], d["modo"], d["caminho"], d["largura"], d["altura"], d["bytes"],
                       d.get("duracao"), d.get("url") or "", mini))
    cid = cur.lastrowid
    con.execute("DELETE FROM capturas WHERE id <= ?", (cid - MAX_CAPTURAS,))  # só o histórico; os arquivos ficam
    con.commit()
    con.close()
    return cid


def captura_atualizar(cid, campos, mini=None):
    sets = {k: v for k, v in campos.items() if k in ("url", "largura", "altura", "bytes", "caminho", "favorito", "tags")}
    if mini is not None:
        sets["mini"] = mini
    if not sets:
        return
    con = conectar()
    con.execute(f"UPDATE capturas SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), int(cid)))
    con.commit()
    con.close()


def capturas_listar(limite=300):
    con = conectar()
    r = con.execute(f"SELECT {_CAMPOS_CAP} FROM capturas ORDER BY id DESC LIMIT ?", (int(limite),)).fetchall()
    con.close()
    return [_sem_mini(x) for x in r]


def captura(cid):
    con = conectar()
    r = con.execute(f"SELECT {_CAMPOS_CAP} FROM capturas WHERE id=?", (int(cid),)).fetchone()
    con.close()
    return _sem_mini(r) if r else None


def captura_mini(cid):
    con = conectar()
    r = con.execute("SELECT mini FROM capturas WHERE id=?", (int(cid),)).fetchone()
    con.close()
    return r[0] if r else None


def capturas_stats(dias=30):
    """Totais do histórico: por tipo, por modo, por dia (últimos `dias`), por site de envio e espaço."""
    from urllib.parse import urlparse
    con = conectar()
    n, tam, env, fav = con.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0), COALESCE(SUM(url <> ''), 0), "
                                   "COALESCE(SUM(favorito), 0) FROM capturas").fetchone()
    por_tipo = dict(con.execute("SELECT tipo, COUNT(*) FROM capturas GROUP BY tipo").fetchall())
    por_modo = dict(con.execute("SELECT modo, COUNT(*) FROM capturas GROUP BY modo ORDER BY 2 DESC").fetchall())
    por_dia = con.execute("SELECT date(ts, 'unixepoch', 'localtime') d, COUNT(*) FROM capturas WHERE ts >= ? "
                          "GROUP BY d ORDER BY d", (int(time.time() - dias * 86400),)).fetchall()
    sites = {}
    for (u,) in con.execute("SELECT url FROM capturas WHERE url <> ''"):
        h = urlparse(u).hostname or "?"
        sites[h] = sites.get(h, 0) + 1
    primeira = con.execute("SELECT MIN(ts) FROM capturas").fetchone()[0]
    con.close()
    return {"total": n, "bytes": tam, "enviadas": env, "favoritas": fav, "por_tipo": por_tipo, "por_modo": por_modo,
            "por_dia": [list(x) for x in por_dia], "sites": dict(sorted(sites.items(), key=lambda x: -x[1])),
            "desde": primeira}

def captura_apagar(cid):
    con = conectar()
    con.execute("DELETE FROM capturas WHERE id=?", (int(cid),))
    con.commit()
    con.close()


# ---------------------------------------------------------------- vazamento de memória
def vazamentos(horas=3, min_mb_h=200, min_crescimento_mb=400, min_r2=0.75):
    """Programas cuja memória cresce de forma contínua (típico de vazamento).
    Usa regressão linear da memória nas últimas horas."""
    desde = int(time.time() - horas * 3600)
    con = conectar()
    nomes = [r[0] for r in con.execute(
        "SELECT nome FROM apps WHERE ts>=? AND nome!=? GROUP BY nome HAVING COUNT(*)>=40 AND MAX(ts)-MIN(ts)>=5400",
        (desde, OUTROS)).fetchall()]
    out = []
    for nome in nomes:
        pts = con.execute("SELECT ts, mem FROM apps WHERE nome=? AND ts>=? ORDER BY ts", (nome, desde)).fetchall()
        n = len(pts)
        xs = [(t - pts[0][0]) / 3600 for t, _ in pts]
        ys = [m / 2**20 for _, m in pts]
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 0:
            continue
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        a = sxy / sxx
        syy = sum((y - my) ** 2 for y in ys)
        r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0
        k = max(3, n // 10)
        ini = sorted(ys[:k])[k // 2]
        fim = sorted(ys[-k:])[k // 2]
        if a >= min_mb_h and r2 >= min_r2 and fim - ini >= min_crescimento_mb:
            passo = max(1, n // 60)
            out.append({"nome": nome, "taxa_mb_h": round(a), "r2": round(r2, 2), "inicio_mb": round(ini), "atual_mb": round(fim),
                        "horas": round(xs[-1], 1), "pontos": [[pts[i][0], round(ys[i])] for i in range(0, n, passo)]})
    con.close()
    out.sort(key=lambda v: v["taxa_mb_h"], reverse=True)
    return out


# ---------------------------------------------------------------- VPS
def vps_gravar(linhas):
    con = conectar()
    con.execute("CREATE TABLE IF NOT EXISTS vps_hist(ts INTEGER, id TEXT, cpu REAL, mem_pct REAL, disco_pct REAL, "
                "load1 REAL, rx REAL, tx REAL, online INTEGER)")
    con.executemany("INSERT INTO vps_hist VALUES (?,?,?,?,?,?,?,?,?)", linhas)
    con.commit()
    con.close()


def vps_historico(vid, horas=24):
    desde = int(time.time() - horas * 3600)
    p = _passo(horas * 3600, 300)
    con = conectar()
    try:
        r = con.execute("SELECT (ts/?)*? b, AVG(cpu), MAX(mem_pct), MAX(disco_pct), AVG(load1), AVG(rx), AVG(tx), MIN(online) "
                        "FROM vps_hist WHERE id=? AND ts>=? GROUP BY b ORDER BY b", (p, p, vid, desde)).fetchall()
    except sqlite3.OperationalError:
        r = []
    con.close()
    return {"desde": desde, "ate": int(time.time()), "passo": p, "pontos": r}


def vps_limpar(dias):
    con = conectar()
    try:
        con.execute("DELETE FROM vps_hist WHERE ts < ?", (int(time.time() - dias * 86400),))
        con.commit()
    except sqlite3.OperationalError:
        pass
    con.close()
