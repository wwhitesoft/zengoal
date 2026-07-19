"""ZenGoal — Speak your business idea. Watch AI agents build it.

Stanford x DeepMind Hackathon, Jul 19 2026.
Voice idea -> ONE goal -> autonomous agent pipeline -> hosted result.
Toy-scale rebuild of the Codezen autonomous loop, powered entirely by Gemini.
"""
import json
import os
import re
import threading
import time
import uuid

from flask import Flask, jsonify, render_template_string, request

import google.generativeai as genai

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
model = genai.GenerativeModel(MODEL_NAME)

app = Flask(__name__)

GOALS = {}  # goal_id -> state dict (in-memory, demo scale)


def _extract_json(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else json.loads(text)


def log(g, msg):
    g["logs"].append({"t": time.strftime("%H:%M:%S"), "msg": msg})


PLANNER_PROMPT = """You are ZenGoal, an autonomous AI chief-of-staff (like the system that runs a real
software company, Codezen). A founder just sent you a spoken business idea.

Idea transcript:
---
{idea}
---

1. Distill it into ONE clear, actionable goal (a single sentence, imperative, in English).
2. Decompose the goal into 4 to 6 concrete tasks that AI agents can execute to produce a
   REAL deliverable: a polished single-page website (landing page / one-pager) for this idea,
   ending with a final "Assemble & ship" task.
3. Assign each task an agent role: one of "Strategist", "Designer", "Copywriter", "Developer", "QA".

Return ONLY JSON:
{{"goal": "...", "idea_summary": "...",
  "tasks": [{{"title": "...", "description": "...", "agent": "Designer"}}]}}"""

WORKER_PROMPT = """You are the {agent} agent working inside ZenGoal's autonomous pipeline.

Overall goal: {goal}
Idea summary: {summary}

Work so far (output of previous agents):
---
{context}
---

Your task now: {title} — {description}

Execute the task for real. Be concrete and production-quality, not meta.
- Strategist: bullet decisions (positioning, sections, CTA).
- Designer: describe layout, color palette (hex), typography, section order.
- Copywriter: write the actual headline, subheadline, section copy, CTA text.
- Developer: you will write code in the final assemble task; here prepare structure notes.
Keep it under 300 words. Output plain text."""

ASSEMBLER_PROMPT = """You are the Developer agent. Final task: assemble and ship.

Goal: {goal}

Using ALL the work below from the other agents (strategy, design, copy):
---
{context}
---

Write a COMPLETE, polished, self-contained single-file HTML landing page implementing it.
Rules: inline CSS in <style>, modern look (hero, sections, CTA button, footer), fully
responsive, no external assets except Google Fonts, no javascript frameworks (vanilla only,
small touches ok), REAL copy from the Copywriter (never lorem ipsum).
Return ONLY the HTML document, starting with <!doctype html>."""


def run_pipeline(goal_id):
    g = GOALS[goal_id]
    try:
        context = ""
        for i, task in enumerate(g["tasks"]):
            task["status"] = "running"
            log(g, f"[{task['agent']}] started: {task['title']}")
            is_last = i == len(g["tasks"]) - 1
            if is_last:
                prompt = ASSEMBLER_PROMPT.format(goal=g["goal"], context=context)
            else:
                prompt = WORKER_PROMPT.format(
                    agent=task["agent"], goal=g["goal"], summary=g["idea_summary"],
                    context=context or "(you are the first agent)",
                    title=task["title"], description=task["description"])
            out = model.generate_content(prompt).text
            if is_last:
                html = re.sub(r"^```(html)?|```$", "", out.strip(), flags=re.MULTILINE).strip()
                g["artifact"] = html
                task["output"] = "Deliverable assembled and shipped to preview URL."
            else:
                task["output"] = out
                context += f"\n\n### {task['agent']} — {task['title']}\n{out}"
            task["status"] = "done"
            log(g, f"[{task['agent']}] done: {task['title']}")
        g["status"] = "awaiting_approval"
        log(g, f"Goal completed — Job {goal_id[:8]} — awaiting your approval.")
    except Exception as e:  # demo resilience
        g["status"] = "failed"
        log(g, f"Pipeline error: {e}")


@app.route("/idea", methods=["POST"])
def idea():
    goal_id = uuid.uuid4().hex
    g = {"id": goal_id, "status": "planning", "goal": None, "idea_summary": None,
         "transcript": None, "tasks": [], "logs": [], "artifact": None,
         "created": time.time()}
    GOALS[goal_id] = g

    audio = request.files.get("audio")
    text = (request.form.get("text") or "").strip()

    def plan():
        try:
            if audio_bytes:
                log(g, "Voice memo received — Gemini transcribing...")
                resp = model.generate_content([
                    "Transcribe this voice memo exactly. Return only the transcript text.",
                    {"mime_type": audio_mime, "data": audio_bytes}])
                transcript = resp.text.strip()
            else:
                transcript = text
            g["transcript"] = transcript
            log(g, f'Idea captured: "{transcript[:140]}"')
            log(g, "Planner (Gemini) distilling goal and building pipeline...")
            plan = _extract_json(model.generate_content(
                PLANNER_PROMPT.format(idea=transcript)).text)
            g["goal"] = plan["goal"]
            g["idea_summary"] = plan.get("idea_summary", "")
            g["tasks"] = [{"title": t["title"], "description": t["description"],
                           "agent": t.get("agent", "Developer"), "status": "queued",
                           "output": None} for t in plan["tasks"]]
            log(g, f"Goal set: {g['goal']}")
            log(g, f"Pipeline created: {len(g['tasks'])} tasks. Agents dispatched.")
            g["status"] = "running"
            run_pipeline(goal_id)
        except Exception as e:
            g["status"] = "failed"
            log(g, f"Planning error: {e}")

    audio_bytes = audio.read() if audio else None
    audio_mime = (audio.mimetype if audio else None) or "audio/ogg"
    if not audio_bytes and not text:
        return jsonify({"error": "send an audio file or text"}), 400
    threading.Thread(target=plan, daemon=True).start()
    return jsonify({"goal_id": goal_id})


@app.route("/api/goal/<goal_id>")
def goal_state(goal_id):
    g = GOALS.get(goal_id)
    if not g:
        return jsonify({"error": "not found"}), 404
    return jsonify({k: g[k] for k in
                    ("id", "status", "goal", "idea_summary", "transcript", "tasks", "logs")}
                   | {"has_artifact": bool(g["artifact"])})


@app.route("/api/goal/<goal_id>/approve", methods=["POST"])
def approve(goal_id):
    g = GOALS.get(goal_id)
    if not g:
        return jsonify({"error": "not found"}), 404
    g["status"] = "approved"
    log(g, "Approved by founder. Shipped.")
    return jsonify({"ok": True})


@app.route("/preview/<goal_id>")
def preview(goal_id):
    g = GOALS.get(goal_id)
    if not g or not g["artifact"]:
        return "Artifact not ready yet.", 404
    return g["artifact"]


@app.route("/health")
def health():
    return jsonify({"ok": True, "model": MODEL_NAME})


@app.route("/")
def index():
    return render_template_string(PAGE)


PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZenGoal — speak it, ship it</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  :root { --bg:#0b0b14; --card:#15151f; --line:#2a2a3a; --acc:#7c5cff; --ok:#34d399; --run:#fbbf24; }
  * { box-sizing:border-box; }
  body { font-family:Inter,-apple-system,sans-serif; background:var(--bg); color:#eee; margin:0; }
  .wrap { max-width:980px; margin:0 auto; padding:32px 20px; }
  h1 { font-size:2rem; font-weight:800; margin:0; } .acc { color:var(--acc); }
  .sub { color:#9a9ab0; margin:6px 0 26px; }
  .panel { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px; margin-bottom:18px; }
  button { background:var(--acc); color:#fff; border:0; border-radius:10px; padding:12px 22px; font-size:1rem; font-weight:600; cursor:pointer; }
  button.rec { background:#e5484d; }
  button:disabled { opacity:.5; cursor:default; }
  textarea { width:100%; height:64px; background:#101018; color:#eee; border:1px solid var(--line); border-radius:10px; padding:10px; font-size:.95rem; }
  .goalbox { font-size:1.15rem; font-weight:600; }
  .task { border:1px solid var(--line); border-radius:10px; padding:12px 14px; margin:10px 0; background:#101018; }
  .task .head { display:flex; align-items:center; gap:10px; }
  .badge { font-size:.72rem; padding:2px 9px; border-radius:20px; border:1px solid var(--line); color:#9a9ab0; }
  .badge.running { color:#000; background:var(--run); border-color:var(--run); }
  .badge.done { color:#000; background:var(--ok); border-color:var(--ok); }
  .agent { font-size:.78rem; color:var(--acc); font-weight:600; }
  .out { font-size:.82rem; color:#b6b6c8; white-space:pre-wrap; margin-top:8px; max-height:130px; overflow:auto; }
  .logs { font-family:ui-monospace,monospace; font-size:.8rem; color:#8f8fa8; max-height:180px; overflow:auto; }
  .approval { border:1px solid var(--acc); background:#191330; border-radius:14px; padding:18px; display:none; }
  a.preview { color:var(--ok); font-weight:700; }
  .hidden { display:none; }
  .pulse { animation:p 1.2s infinite; } @keyframes p { 50% { opacity:.45; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>Zen<span class="acc">Goal</span></h1>
  <div class="sub">Speak your business idea. One goal is set, an AI agent pipeline builds it, you get the result. Powered by Gemini.</div>

  <div class="panel" id="inputPanel">
    <b>1 · Send your idea (voice, like a WhatsApp memo)</b><br><br>
    <button id="recBtn">&#127908; Hold nothing — click to record</button>
    <span id="recState" style="margin-left:10px;color:#9a9ab0"></span>
    <div style="margin:14px 0 6px;color:#9a9ab0;font-size:.85rem">…or upload an audio file / type it:</div>
    <input type="file" id="audioFile" accept="audio/*" style="color:#9a9ab0">
    <textarea id="textIdea" placeholder="I want to launch a specialty coffee subscription for remote workers..."></textarea><br>
    <button id="goBtn" style="margin-top:10px">&#9889; Set the goal</button>
  </div>

  <div class="panel hidden" id="goalPanel">
    <div style="color:#9a9ab0;font-size:.8rem">TRANSCRIPT</div>
    <div id="transcript" style="font-style:italic;color:#c8c8da;margin:4px 0 14px"></div>
    <div style="color:#9a9ab0;font-size:.8rem">GOAL <span id="statusBadge" class="badge"></span></div>
    <div class="goalbox acc" id="goalText" ></div>
  </div>

  <div class="panel hidden" id="boardPanel">
    <b>2 · Agent pipeline</b>
    <div id="tasks"></div>
  </div>

  <div class="approval" id="approvalPanel">
    <b>&#128276; Goal completed — awaiting your approval</b>
    <div style="margin:8px 0">Your deliverable is live: <a class="preview" id="previewLink" target="_blank">open the shipped result &rarr;</a></div>
    <button id="approveBtn">&#9989; Approve &amp; ship</button>
    <span id="approvedMsg" class="hidden" style="color:var(--ok);font-weight:700;margin-left:10px">Shipped. It has manners, too.</span>
  </div>

  <div class="panel">
    <b>Live log</b>
    <div class="logs" id="logs"></div>
  </div>
</div>

<script>
let goalId = null, mediaRec = null, chunks = [], recording = false, recBlob = null;

const $ = id => document.getElementById(id);

$('recBtn').onclick = async () => {
  if (!recording) {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    mediaRec = new MediaRecorder(stream); chunks = [];
    mediaRec.ondataavailable = e => chunks.push(e.data);
    mediaRec.onstop = () => { recBlob = new Blob(chunks, {type: mediaRec.mimeType}); $('recState').textContent = 'Voice memo ready ✓'; };
    mediaRec.start(); recording = true;
    $('recBtn').textContent = '⏹ Stop recording'; $('recBtn').classList.add('rec');
    $('recState').textContent = 'Recording…';
  } else {
    mediaRec.stop(); mediaRec.stream.getTracks().forEach(t=>t.stop()); recording = false;
    $('recBtn').textContent = '🎤 Record again'; $('recBtn').classList.remove('rec');
  }
};

$('goBtn').onclick = async () => {
  const fd = new FormData();
  const f = $('audioFile').files[0];
  if (recBlob) fd.append('audio', recBlob, 'memo.webm');
  else if (f) fd.append('audio', f, f.name);
  else if ($('textIdea').value.trim()) fd.append('text', $('textIdea').value.trim());
  else return alert('Record, upload or type your idea first.');
  $('goBtn').disabled = true; $('goBtn').textContent = 'Working…';
  const r = await fetch('/idea', {method:'POST', body:fd});
  goalId = (await r.json()).goal_id;
  poll();
};

async function poll() {
  const r = await fetch('/api/goal/' + goalId);
  const g = await r.json();
  if (g.transcript) { $('goalPanel').classList.remove('hidden'); $('transcript').textContent = '“' + g.transcript + '”'; }
  if (g.goal) { $('goalText').textContent = g.goal; }
  $('statusBadge').textContent = g.status; $('statusBadge').className = 'badge ' + (g.status==='running'?'running':g.status==='awaiting_approval'||g.status==='approved'?'done':'');
  if (g.tasks.length) {
    $('boardPanel').classList.remove('hidden');
    $('tasks').innerHTML = g.tasks.map(t => `
      <div class="task"><div class="head">
        <span class="badge ${t.status} ${t.status==='running'?'pulse':''}">${t.status}</span>
        <span class="agent">${t.agent}</span> <b>${t.title}</b></div>
        ${t.output ? `<div class="out">${t.output.replace(/</g,'&lt;')}</div>` : ''}
      </div>`).join('');
  }
  $('logs').innerHTML = g.logs.map(l => `<div>[${l.t}] ${l.msg}</div>`).join('');
  $('logs').scrollTop = 1e9;
  if (g.has_artifact) {
    $('approvalPanel').style.display = 'block';
    $('previewLink').href = '/preview/' + goalId;
  }
  if (g.status !== 'awaiting_approval' && g.status !== 'approved' && g.status !== 'failed') setTimeout(poll, 1200);
}

$('approveBtn').onclick = async () => {
  await fetch('/api/goal/' + goalId + '/approve', {method:'POST'});
  $('approvedMsg').classList.remove('hidden');
};
</script>
</body>
</html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
