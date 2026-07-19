// ZenGoal WhatsApp bridge — Baileys.
// Send a voice memo with your business idea on WhatsApp:
// it becomes a goal, an AI agent pipeline executes it, you get the shipped result back
// (text + voice reply). Experience mirrors a real autonomous chief-of-staff.
import makeWASocket, {
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  DisconnectReason,
  downloadMediaMessage,
} from '@whiskeysockets/baileys';
import express from 'express';
import pino from 'pino';
import QRCode from 'qrcode';
import { execFile } from 'child_process';
import { writeFile, readFile, unlink } from 'fs/promises';
import os from 'os';
import path from 'path';

const ZENGOAL_URL = process.env.ZENGOAL_URL || 'https://zengoal-283636345380.us-central1.run.app';
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const AUTH_DIR = process.env.AUTH_DIR || '/tmp/wa-auth';
const PORT = process.env.PORT || 8080;

const logger = pino({ level: 'warn' });
let sock = null;
let lastQR = null;
let connected = false;
const activeGoals = new Map(); // jid -> goal_id (last one, for "approve")

// ---------- Gemini TTS: text -> WhatsApp voice note (ogg/opus) ----------
async function ttsVoiceNote(text) {
  const r = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:generateContent?key=${GEMINI_API_KEY}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contents: [{ parts: [{ text }] }],
        generationConfig: {
          responseModalities: ['AUDIO'],
          speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: 'Kore' } } },
        },
      }),
    },
  );
  const j = await r.json();
  const b64 = j?.candidates?.[0]?.content?.parts?.[0]?.inlineData?.data;
  if (!b64) throw new Error('TTS: no audio in response');
  const pcm = path.join(os.tmpdir(), `tts-${Date.now()}.pcm`);
  const ogg = pcm.replace('.pcm', '.ogg');
  await writeFile(pcm, Buffer.from(b64, 'base64'));
  await new Promise((res, rej) =>
    execFile('ffmpeg', ['-f', 's16le', '-ar', '24000', '-ac', '1', '-i', pcm,
      '-c:a', 'libopus', '-b:a', '32k', ogg], e => (e ? rej(e) : res())),
  );
  const buf = await readFile(ogg);
  unlink(pcm).catch(() => {}); unlink(ogg).catch(() => {});
  return buf;
}

async function sendVoice(jid, text) {
  try {
    const audio = await ttsVoiceNote(text);
    await sock.sendMessage(jid, { audio, mimetype: 'audio/ogg; codecs=opus', ptt: true });
  } catch (e) {
    console.error('voice reply failed, falling back to text:', e.message);
    await sock.sendMessage(jid, { text });
  }
}

const sendText = (jid, text) => sock.sendMessage(jid, { text });

// ---------- ZenGoal pipeline driver ----------
async function runIdea(jid, { audioBuf, mime, text }) {
  const fd = new FormData();
  if (audioBuf) fd.append('audio', new Blob([audioBuf], { type: mime }), 'memo.ogg');
  else fd.append('text', text);
  const r = await fetch(`${ZENGOAL_URL}/idea`, { method: 'POST', body: fd });
  const { goal_id } = await r.json();
  activeGoals.set(jid, goal_id);

  let announcedGoal = false, announcedTasks = false;
  const doneTasks = new Set();
  for (let i = 0; i < 200; i++) {
    await new Promise(res => setTimeout(res, 3000));
    const g = await (await fetch(`${ZENGOAL_URL}/api/goal/${goal_id}`)).json();

    if (g.goal && !announcedGoal) {
      announcedGoal = true;
      await sendText(jid, `🎯 Goal set:\n"${g.goal}"`);
    }
    if (g.tasks?.length && !announcedTasks) {
      announcedTasks = true;
      const list = g.tasks.map(t => `• [${t.agent}] ${t.title}`).join('\n');
      await sendText(jid, `🧩 Pipeline created — ${g.tasks.length} tasks, agents dispatched:\n${list}`);
    }
    for (const t of g.tasks || []) {
      if (t.status === 'done' && !doneTasks.has(t.title)) {
        doneTasks.add(t.title);
        await sendText(jid, `✅ [${t.agent}] ${t.title} — done`);
      }
    }
    if (g.status === 'awaiting_approval') {
      const preview = `${ZENGOAL_URL}/preview/${goal_id}`;
      await sendText(jid,
        `🔔 Goal completed — Job ${goal_id.slice(0, 8)} — awaiting your approval.\n\n` +
        `Your deliverable is live: ${preview}\n\nReply "approve" to ship it.`);
      await sendVoice(jid,
        `Goal completed. Your deliverable is built and live at the preview link. ` +
        `I need your approval to ship it. I do the work — you keep the manners.`);
      return;
    }
    if (g.status === 'failed') {
      await sendText(jid, `❌ Pipeline failed: ${g.logs?.at(-1)?.msg || 'unknown error'}`);
      return;
    }
  }
  await sendText(jid, '⏱️ Pipeline timed out.');
}

// ---------- WhatsApp wiring ----------
async function startWA() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  sock = makeWASocket({ version, auth: state, logger, printQRInTerminal: false, browser: ['ZenGoal', 'Chrome', '1.0'] });
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', ({ connection, lastDisconnect, qr }) => {
    if (qr) { lastQR = qr; connected = false; }
    if (connection === 'open') { connected = true; lastQR = null; console.log('WA connected'); }
    if (connection === 'close') {
      connected = false;
      const code = lastDisconnect?.error?.output?.statusCode;
      if (code !== DisconnectReason.loggedOut) setTimeout(startWA, 2000);
      else console.log('WA logged out — new QR needed at /qr');
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const m of messages) {
      try {
        if (m.key.fromMe || !m.message) continue;
        const jid = m.key.remoteJid;
        if (jid.endsWith('@g.us') || jid === 'status@broadcast') continue;

        const audioMsg = m.message.audioMessage;
        const text = m.message.conversation || m.message.extendedTextMessage?.text || '';

        if (!audioMsg && /^(approve|approva|ok ship|ship it)$/i.test(text.trim())) {
          const gid = activeGoals.get(jid);
          if (gid) {
            await fetch(`${ZENGOAL_URL}/api/goal/${gid}/approve`, { method: 'POST' });
            await sendText(jid, `🚀 Approved and shipped. Live: ${ZENGOAL_URL}/preview/${gid}`);
          } else await sendText(jid, 'Nothing pending approval. Send me a voice memo with your idea!');
          continue;
        }

        if (audioMsg) {
          await sendText(jid, '🎙️ Voice memo received — transcribing with Gemini and setting your goal...');
          const buf = await downloadMediaMessage(m, 'buffer', {});
          runIdea(jid, { audioBuf: buf, mime: audioMsg.mimetype?.split(';')[0] || 'audio/ogg' })
            .catch(e => sendText(jid, `❌ ${e.message}`));
        } else if (text.trim()) {
          await sendText(jid, '📝 Idea received — setting your goal...');
          runIdea(jid, { text }).catch(e => sendText(jid, `❌ ${e.message}`));
        }
      } catch (e) { console.error('msg handler:', e); }
    }
  });
}

// ---------- HTTP: health + QR pairing page ----------
const app = express();
app.get('/health', (_, res) => res.json({ ok: true, connected }));
app.get('/qr', async (_, res) => {
  if (connected) return res.send('<h2 style="font-family:sans-serif">✅ WhatsApp connected — send a voice memo!</h2>');
  if (!lastQR) return res.send('<meta http-equiv="refresh" content="2"><p>Waiting for QR…</p>');
  const png = await QRCode.toDataURL(lastQR, { width: 360 });
  res.send(`<meta http-equiv="refresh" content="15"><div style="text-align:center;font-family:sans-serif">
    <h2>Scan with WhatsApp → Linked devices</h2><img src="${png}"></div>`);
});
app.listen(PORT, () => console.log('bridge http on', PORT));

startWA().catch(e => { console.error(e); process.exit(1); });
