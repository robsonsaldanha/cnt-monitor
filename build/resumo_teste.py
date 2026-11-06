"""Resume o teste automático feito no Windows (GitHub Actions).
  python build/resumo_teste.py teste-console.json
  python build/resumo_teste.py --api pasta_com_api_json
"""
import os
import sys
import json


def carregar(args):
    if args[0] == "--api":
        pasta = args[1]
        d = {}
        for nome in ("status", "agora", "rede", "hardware"):
            p = os.path.join(pasta, f"api-{nome}.json")
            if os.path.exists(p):
                with open(p, encoding="utf-8-sig") as f:
                    d[nome] = json.load(f)
        return d
    with open(args[0], encoding="utf-8") as f:
        return json.load(f)


def main():
    d = carregar(sys.argv[1:])
    st, ag, rd, hw = d.get("status", {}), d.get("agora") or {}, d.get("rede", {}), d.get("hardware", {})
    falhas = []
    print("== STATUS ==")
    for k in ("versao", "admin", "modo_coleta", "tempo_amostra_ms", "tempo_amostra_max_ms", "gpu_ok", "gpu_erro", "gpus", "bandeja_online", "rede_ok", "rede_erro", "eventos_rede", "etw", "sensores", "avisos", "pawnio", "tempo_amostra", "erros"):
        print(f"  {k}: {st.get(k)}")
    print("== AGORA ==")
    print(f"  cpu={ag.get('cpu')} mem={ag.get('mem_pct')} nproc={ag.get('nproc')} nconn={ag.get('nconn')} "
          f"temp_cpu={ag.get('temp_cpu')} temp_gpu={ag.get('temp_gpu')} temp_disco={ag.get('temp_disco')}")
    print(f"  discos={[(x['nome'], x['letras'], x['ler'], x['gravar'], x['ativo']) for x in ag.get('discos', [])]}")
    print(f"  gpus={[(x['nome'], x['uso'], x['mem_ded_usada']) for x in ag.get('gpus', [])]}")
    progs = [g for g in ag.get("programas", []) if not g.get("outros")]
    print(f"  programas: {len(progs)}")
    for g in sorted(progs, key=lambda g: g["mem"], reverse=True)[:12]:
        print(f"    {g['nome'][:34]:34} mem={g['mem']/2**20:8.1f}MB cpu={g['cpu']:5.1f} proc={g['nproc']:3} "
              f"env={g['env'] or 0:9.0f} rec={g['rec'] or 0:9.0f} conn={g['nconn']} ips={g['nips']} prot={g.get('protegido')}")
    print("== REDE ==")
    print(f"  ativo={rd.get('ativo')} erro={rd.get('erro')} conexoes={len(rd.get('conexoes', []))} remotos={len(rd.get('remotos', []))}")
    for r in rd.get("remotos", [])[:12]:
        print(f"    {r['programa'][:26]:26} {r['ip']:40} host={r.get('host')} env={r['env_tot']} rec={r['rec_tot']}")
    print("== HARDWARE ==")
    print(f"  fontes={hw.get('fontes')} avisos={hw.get('avisos')} pawnio={hw.get('pawnio')} cpu={hw.get('cpu_nome')}")
    sens = hw.get("sensores", [])
    print(f"  sensores: {len(sens)}")
    for s in sens[:40]:
        print(f"    [{s['tipo_hw']}] {s['hw'][:30]:30} {s['nome'][:24]:24} {s['tipo']:12} {s['valor']} {s['un']} ({s['fonte']})")

    if d.get("inicializacao") is not None:
        print("== INICIALIZAÇÃO ==")
        for i in d["inicializacao"][:20]:
            print(f"    {i['nome'][:30]:30} ativo={i['ativo']} {i['origem']} | {i['escopo']} | {i['comando'][:70]}")
    if d.get("inicializacao_erro"):
        print("ERRO inicialização:", d["inicializacao_erro"])
    if d.get("processos"):
        print("== AMOSTRA DE PROCESSOS ==")
        for p in sorted(d["processos"], key=lambda x: -x["mem"])[:12]:
            print(f"    {p['nome'][:28]:28} pid={p['pid']:6} prog={p['programa'][:22]:22} mem={p['mem']/2**20:7.1f}MB ws={p['ws']/2**20:7.1f}MB cpu={p['cpu']:5.1f} thr={p['threads']} h={p['handles']} prot={p['protegido']}")
    if not ag:
        falhas.append("sem estado ao vivo")
    if len(progs) < 5:
        falhas.append("poucos programas")
    if st.get("erros"):
        print("== ERROS ==")
        for e in st["erros"]:
            print(e)
    if falhas:
        print("FALHAS:", falhas)
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
