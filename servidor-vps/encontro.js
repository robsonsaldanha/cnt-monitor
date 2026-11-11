'use strict';
// Ponto de encontro do acesso remoto do CNT Monitor.
//
// Só apresenta os dois computadores: guarda por alguns minutos o convite e a resposta (a oferta e a resposta
// WebRTC) e entrega ao outro lado. Não vê a tela nem os comandos, que vão direto de um PC ao outro, e não
// consegue se passar por nenhum deles: a senha nunca passa por aqui, e a prova dela é amarrada aos
// certificados da conexão (src/monitorpc/remoto.py). Nada é gravado em disco.
//
//   PC atendido:  POST /api/registrar {id, chave}  ->  GET /api/esperar?id&chave (espera longa)  ->  POST /api/enviar
//   técnico:      POST /api/conectar {id}  ->  GET /api/receber?pedido (espera longa)  ->  POST /api/responder
//
// Variáveis: PORTA (padrão 4070), TURN_SEGREDO (o mesmo static-auth-secret do coturn), TURN_HOST,
// ECO_PORTA (padrão 48790/udp; "0" desliga).
//
// Eco UDP: o CNT Monitor mede a saída UDP da internet de quem testa quando o destino de um teste não devolve
// nada. Devolve o mesmo datagrama (nunca maior: não serve para amplificar ataque), só os que começam com "CNTE"
// e têm até 64 bytes, e no máximo 20 por segundo por endereço.
const http = require('http');
const dgram = require('dgram');
const https = require('https');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PORTA = parseInt(process.env.PORTA || '4070', 10);
const ECO_PORTA = parseInt(process.env.ECO_PORTA || '48790', 10);
const TURN_SEGREDO = process.env.TURN_SEGREDO || '';
const TURN_HOST = process.env.TURN_HOST || 'turn.cntoficial.com.br';
const TURN_VALIDADE = 12 * 3600;
const ESPERA_MS = 25000;            // espera longa: devolve vazio depois disto
const SEM_SINAL_MS = 90000;         // PC que parou de perguntar sai da lista
const PEDIDO_MS = 15 * 60000;       // um pedido de conexão dura no máximo isto
const MAX_CORPO = 64 * 1024;
const MAX_FILA = 20;
const RE_ID = /^\d{9}$/;
const RE_CHAVE = /^[A-Za-z0-9_-]{32,64}$/;
const RE_PEDIDO = /^[A-Za-z0-9_-]{22,64}$/;

const pcs = new Map();       // id -> {chave, fila, espera, visto}
const pedidos = new Map();   // pedido -> {id, fila, espera, criado}

// ---------------------------------------------------------------- limite por endereço
const contas = new Map();    // "ip|tipo" -> [inicio, n]
const LIMITES = { registrar: [60000, 30], conectar: [600000, 30], id: [600000, 15], outro: [60000, 600] };
function passou(chave, tipo) {
  const [janela, max] = LIMITES[tipo];
  const k = tipo + '|' + chave, agora = Date.now(), c = contas.get(k);
  if (!c || agora - c[0] > janela) { contas.set(k, [agora, 1]); return true; }
  return ++c[1] <= max;
}

function turn() {
  if (!TURN_SEGREDO) return null;
  const usuario = `${Math.floor(Date.now() / 1000) + TURN_VALIDADE}:cntmonitor`;
  const senha = crypto.createHmac('sha1', TURN_SEGREDO).update(usuario).digest('base64');
  return { urls: [`turn:${TURN_HOST}:3478?transport=udp`, `turn:${TURN_HOST}:3478?transport=tcp`], username: usuario, credential: senha };
}

// ---------------------------------------------------------------- filas com espera longa
function entregar(caixa, msg) {
  if (caixa.fila.length >= MAX_FILA) caixa.fila.shift();
  caixa.fila.push(msg);
  if (caixa.espera) { const r = caixa.espera; caixa.espera = null; r(); }
}
function aguardar(caixa, res) {
  let t = null;
  const fim = () => { clearTimeout(t); if (caixa.espera === fim) caixa.espera = null; responder(res, 200, { msgs: caixa.fila.splice(0) }); };
  if (caixa.fila.length) return fim();
  if (caixa.espera) caixa.espera();              // uma espera por caixa: a anterior volta vazia
  t = setTimeout(fim, ESPERA_MS);
  caixa.espera = fim;
  res.on('close', () => { if (caixa.espera === fim) { caixa.espera = null; clearTimeout(t); } });
}

setInterval(() => {
  const agora = Date.now();
  for (const [id, pc] of pcs) if (!pc.espera && agora - pc.visto > SEM_SINAL_MS) pcs.delete(id);
  for (const [p, d] of pedidos) if (agora - d.criado > PEDIDO_MS) { if (d.espera) d.espera(); pedidos.delete(p); }
  for (const [k, c] of contas) if (agora - c[0] > 600000) contas.delete(k);
}, 10000).unref();

// ---------------------------------------------------------------- download: sempre o instalador mais novo
// Primeiro a cópia da própria VPS (baixar-instalador.py grava DOWNLOADS/ultima.json; o nginx serve /download/);
// sem ela, o arquivo da release no GitHub.
const REPO = 'robsonsaldanha/cnt-monitor';
const DOWNLOADS = process.env.DOWNLOADS || '/var/www/cntmonitor_downloads';
let instalador = { url: `https://github.com/${REPO}/releases/latest`, em: 0 };
function instaladorLocal() {
  try {
    const u = JSON.parse(fs.readFileSync(path.join(DOWNLOADS, 'ultima.json'), 'utf8'));
    if (/^CNTMonitor-Setup-[\d.]+\.exe$/.test(u.arquivo) && fs.existsSync(path.join(DOWNLOADS, u.arquivo))) return '/download/' + u.arquivo;
  } catch (_) { }
  return null;
}
function atualizarInstalador() {
  if (Date.now() - instalador.em < 600000) return;
  instalador.em = Date.now();
  https.get(`https://api.github.com/repos/${REPO}/releases/latest`, { headers: { 'User-Agent': 'cnt-monitor-site', Accept: 'application/vnd.github+json' } }, r => {
    let t = ''; r.setEncoding('utf8'); r.on('data', x => { if (t.length < 2e6) t += x; });
    r.on('end', () => { try { const a = (JSON.parse(t).assets || []).find(x => /^CNTMonitor-Setup-.*\.exe$/.test(x.name)); if (a) instalador.url = a.browser_download_url; } catch (_) { } });
  }).on('error', () => { });
}

// ---------------------------------------------------------------- http
function responder(res, cod, obj) {
  if (res.writableEnded) return;
  const corpo = JSON.stringify(obj);
  res.writeHead(cod, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'X-Content-Type-Options': 'nosniff' });
  res.end(corpo);
}
const erro = (res, cod, msg) => responder(res, cod, { ok: false, erro: msg });

function ler(req) {
  return new Promise((ok, falha) => {
    let n = 0; const partes = [];
    req.on('data', b => { n += b.length; if (n <= MAX_CORPO) partes.push(b); });     // o excesso é lido e jogado fora
    req.on('end', () => { if (n > MAX_CORPO) return falha(new Error('grande')); try { const o = JSON.parse(Buffer.concat(partes).toString('utf8') || '{}'); ok(o && typeof o === 'object' ? o : {}); } catch (e) { falha(e); } });
    req.on('error', falha);
  });
}

function dono(id, chave) {
  const pc = RE_ID.test(String(id)) && pcs.get(String(id));
  if (!pc || typeof chave !== 'string' || chave.length !== pc.chave.length) return null;
  return crypto.timingSafeEqual(Buffer.from(chave), Buffer.from(pc.chave)) ? pc : null;
}

async function tratar(req, res) {
  const url = new URL(req.url, 'http://x');
  const ip = String(req.headers['x-real-ip'] || req.socket.remoteAddress || '');
  const rota = url.pathname.replace(/^\/api/, '');
  if (req.method === 'OPTIONS') return responder(res, 204, {});
  if (rota === '/saude') return responder(res, 200, { ok: true, pcs: pcs.size, pedidos: pedidos.size });
  if (url.pathname === '/baixar') {
    const local = instaladorLocal();
    if (!local) atualizarInstalador();
    res.writeHead(302, { Location: local || instalador.url, 'Cache-Control': 'no-store' });
    return res.end();
  }
  if (!passou(ip, 'outro')) return erro(res, 429, 'Muitas requisições. Espere um minuto.');

  if (req.method === 'GET' && rota === '/esperar') {
    const pc = dono(url.searchParams.get('id'), url.searchParams.get('chave'));
    if (!pc) return erro(res, 404, 'ID não registrado.');
    pc.visto = Date.now();
    return aguardar(pc, res);
  }
  if (req.method === 'GET' && rota === '/receber') {
    const d = pedidos.get(String(url.searchParams.get('pedido')));
    if (!d) return erro(res, 404, 'Pedido de conexão vencido.');
    return aguardar(d, res);
  }
  if (req.method !== 'POST') return erro(res, 404, 'não encontrado');
  let c;
  try { c = await ler(req); } catch (e) { return erro(res, e.message === 'grande' ? 413 : 400, 'Corpo inválido.'); }

  if (rota === '/registrar') {
    if (!passou(ip, 'registrar')) return erro(res, 429, 'Muitas requisições. Espere um minuto.');
    const id = String(c.id || ''), chave = String(c.chave || '');
    if (!RE_ID.test(id) || !RE_CHAVE.test(chave)) return erro(res, 400, 'ID ou chave inválidos.');
    const atual = pcs.get(id);
    if (atual && !dono(id, chave)) return erro(res, 409, 'Esse ID já está em uso.');
    if (!atual) pcs.set(id, { chave, fila: [], espera: null, visto: Date.now() });
    else atual.visto = Date.now();
    return responder(res, 200, { ok: true, turn: turn() });
  }
  if (rota === '/sair') {
    const pc = dono(c.id, c.chave);
    if (pc) { if (pc.espera) pc.espera(); pcs.delete(String(c.id)); }
    return responder(res, 200, { ok: true });
  }
  if (rota === '/enviar') {                         // PC atendido -> técnico
    const pc = dono(c.id, c.chave), d = pedidos.get(String(c.pedido || ''));
    if (!pc) return erro(res, 404, 'ID não registrado.');
    if (!d || d.id !== String(c.id)) return erro(res, 404, 'Pedido de conexão vencido.');
    entregar(d, { dados: c.dados });
    return responder(res, 200, { ok: true });
  }
  if (rota === '/conectar') {                       // técnico chama um ID
    const id = String(c.id || '').replace(/\D/g, '');
    if (!passou(ip, 'conectar') || !passou(id, 'id')) return erro(res, 429, 'Muitas tentativas de conexão. Espere alguns minutos.');
    const pc = RE_ID.test(id) && pcs.get(id);
    if (!pc) return erro(res, 404, 'Não encontrei esse ID. Confira os números e se o CNT Monitor está aberto no outro computador.');
    const pedido = crypto.randomBytes(24).toString('base64url');
    pedidos.set(pedido, { id, fila: [], espera: null, criado: Date.now() });
    entregar(pc, { tipo: 'pedido', pedido });
    return responder(res, 200, { ok: true, pedido, turn: turn() });
  }
  if (rota === '/responder') {                      // técnico -> PC atendido
    const p = String(c.pedido || ''), d = RE_PEDIDO.test(p) && pedidos.get(p);
    if (!d) return erro(res, 404, 'Pedido de conexão vencido.');
    const pc = pcs.get(d.id);
    if (!pc) return erro(res, 404, 'O outro computador saiu do ar.');
    entregar(pc, { tipo: 'msg', pedido: p, dados: c.dados });
    return responder(res, 200, { ok: true });
  }
  return erro(res, 404, 'não encontrado');
}

// ---------------------------------------------------------------- eco UDP
const ecoContas = new Map();     // ip -> [segundo, n]
function eco(msg, ip, agora = Date.now()) {
  if (msg.length < 12 || msg.length > 64 || msg.toString('latin1', 0, 4) !== 'CNTE') return null;
  const s = Math.floor(agora / 1000), c = ecoContas.get(ip);
  if (!c || c[0] !== s) { if (ecoContas.size > 50000) ecoContas.clear(); ecoContas.set(ip, [s, 1]); }
  else if (++c[1] > 20) return null;
  return msg;
}

if (require.main === module) {
  atualizarInstalador();
  http.createServer((req, res) => tratar(req, res).catch(e => { console.error(e); erro(res, 500, 'Erro interno.'); }))
    .listen(PORTA, '127.0.0.1', () => {
      console.log(`encontro ouvindo em 127.0.0.1:${PORTA}${TURN_SEGREDO ? ' (com TURN)' : ' (sem TURN)'}`);
      if (!ECO_PORTA) return;
      const u = dgram.createSocket('udp4');
      u.on('message', (m, r) => { const x = eco(m, r.address); if (x) u.send(x, r.port, r.address); });
      u.on('error', e => console.error('eco UDP:', e.message));
      u.bind(ECO_PORTA, () => console.log(`eco UDP em 0.0.0.0:${ECO_PORTA}`));
    });
}
module.exports = { tratar, pcs, pedidos, eco };
