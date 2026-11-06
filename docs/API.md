# API do Monitor do PC (v1) — somente consulta

Outros programas podem ler tudo o que o Monitor do PC mede. A API **nunca altera nada**: só aceita `GET`.

## Endereço e acesso

| De onde | Endereço | Autenticação |
|---|---|---|
| O próprio PC | `http://127.0.0.1:48731/api/v1/...` | nenhuma |
| Outro aparelho da rede de casa | `http://IP-DO-PC:48732/api/v1/...` | cabeçalho `X-API-Key: <chave>` |

Para usar pela rede: Configurações → **Acesso pelo celular** (ligar, com senha) e **API para outros programas → Gerar chave**.
A chave também pode ir na URL (`?chave=...`), mas prefira o cabeçalho.

Páginas web (JavaScript no navegador) deste PC só conseguem ler a API se “Permitir que páginas web deste PC leiam a API”
estiver ligado (envia `Access-Control-Allow-Origin: *`).

## Formato da resposta

```json
{ "ok": true, "versao_api": 1, "gerado_em": 1793989057.83, "dados": { ... } }
```

Erros: `404` (`{"ok": false, "dados": {"erro": "..."}}`), `401` (sem chave pela rede).
Unidades: bytes para memória/disco/rede, bytes por segundo para taxas, % para uso, °C para temperatura,
segundos Unix (UTC) para datas.

## Recursos

| Caminho | O que devolve |
|---|---|
| `/api/v1` | versão e lista de recursos |
| `/api/v1/resumo` | CPU, núcleos, memória, disco, rede, temperaturas, GPU, nº de processos/threads/conexões |
| `/api/v1/programas` | programas abertos (soma de todos os processos de cada um) |
| `/api/v1/programas/{nome}` | um programa com processos, IPs e conexões (ex.: `/api/v1/programas/Google%20Chrome`) |
| `/api/v1/processos` | todos os processos |
| `/api/v1/processos/{pid}` | um processo com caminho, linha de comando, conexões e IPs |
| `/api/v1/rede` | envio/recebimento total, por programa e por placa de rede |
| `/api/v1/conexoes` | conexões TCP/UDP abertas |
| `/api/v1/ips` | IPs contatados com volume e programas |
| `/api/v1/hardware` | sensores, discos, GPUs, partições, bateria, CPU, sistema |
| `/api/v1/sensores` | todos os sensores (temperatura, ventoinha, clock, consumo…) |
| `/api/v1/temperaturas` | resumo + sensores de temperatura |
| `/api/v1/discos` | discos físicos e unidades (espaço) |
| `/api/v1/gpus` | placas de vídeo |
| `/api/v1/historico?horas=1&programa=Nome` | série histórica (até 720 h) |
| `/api/v1/lentidao?dias=7` | momentos de lentidão e quem pesava |
| `/api/v1/alertas` | últimos alertas disparados |
| `/api/v1/vazamentos` | programas com possível vazamento de memória |
| `/api/v1/inicializacao` | programas que iniciam com o Windows |
| `/api/v1/servicos` | serviços do Windows |
| `/api/v1/energia` | plano e ajustes de energia |
| `/api/v1/jogo` | estado do Modo Jogo e FPS |
| `/api/v1/vps` | servidores cadastrados e últimas leituras (sem senhas) |
| `/api/v1/status` | versão, saúde da coleta, sensores, banco |

## Exemplos

PowerShell:

```powershell
$r = (Invoke-RestMethod http://127.0.0.1:48731/api/v1/resumo).dados
"CPU $($r.cpu)%  Memória $($r.mem_pct)%  CPU $($r.temp_cpu) °C"
```

Python:

```python
import json, urllib.request
d = json.load(urllib.request.urlopen("http://127.0.0.1:48731/api/v1/programas"))["dados"]
for p in sorted(d, key=lambda p: p["mem"], reverse=True)[:5]:
    print(f'{p["nome"]:30} {p["mem"] / 2**30:5.2f} GB  {p["cpu"]:5.1f}% CPU')
```

curl pela rede:

```bash
curl -H "X-API-Key: mpc_SUA_CHAVE" http://192.168.0.10:48732/api/v1/temperaturas
```

JavaScript (página aberta no próprio PC, com a opção de páginas web ligada):

```js
const { dados } = await (await fetch("http://127.0.0.1:48731/api/v1/resumo")).json();
console.log(dados.cpu, dados.mem_pct);
```

## Estabilidade

Os campos listados acima são estáveis na v1. Campos novos podem aparecer; nenhum é removido sem uma `/api/v2`.
