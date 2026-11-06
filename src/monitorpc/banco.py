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
    con.commit()
    con.close()


def limpar(dias=7):
    lim = int(time.time() - dias * 86400)
    con = conectar()
    for t in ("geral", "apps", "procs", "temps"):
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


def apagar_tudo():
    con = conectar()
    for t in ("geral", "apps", "procs", "temps", "remotos"):
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
