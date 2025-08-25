from flask import Flask, render_template, request, redirect, url_for, session, render_template_string, jsonify
import json
import uuid
import os
import re
from datetime import datetime
import requests

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
app.secret_key = '93jsdf983jdfQWEr9023r'

TICKETS_FILE = 'data/tickets.json'

# -----------------------------
# Utilities
# -----------------------------
def load_tickets():
    with open(TICKETS_FILE, 'r') as f:
        return json.load(f)

def save_tickets(tickets):
    with open(TICKETS_FILE, 'w') as f:
        json.dump(tickets, f, indent=4)

def strip_think_tags(text: str) -> str:
    """Hide any <think>…</think> traces if a model emits them."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

def prepare_markdown_text(text: str) -> str:
    """
    Normalize model output so lists render nicely.
    - Put each 'N. ' on its own line
    - Normalize 'Escalate if' header to bold
    - Remove orphan/dangling '**'
    """
    s = strip_think_tags(text)

    # Ensure inline list becomes newline list (": 1." -> ":\n\n1.")
    s = re.sub(r":\s*1\.\s", ":\n\n1. ", s)

    # Make sure every "N. " starts on a new line
    s = re.sub(r"(?<!\n)(\d{1,2})\.\s", r"\n\1. ", s)

    # Normalize "Escalate if" into a bold header
    s = re.sub(r"\bEscalate if\b[:\s]*", "\n\n**Escalate if:** ", s, flags=re.IGNORECASE)

    # Kill stray "**" tokens that sit alone on a line
    s = re.sub(r"(^|\n)\s*\*\*\s*(?=\n|$)", r"\n", s)

    return s.strip()

def md_to_html(text: str) -> str:
    """
    Minimal Markdown -> HTML (bold, lists, paragraphs) without external deps.
    Handles:
      - **bold**
      - ordered (1.) and unordered (- or *) lists
      - paragraphs & blank lines
    Also escapes HTML first to be safe.
    """
    s = prepare_markdown_text(text)

    # escape HTML
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # render bold pairs first
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

    # remove any remaining (unmatched) ** artifacts anywhere
    s = re.sub(r"\*\*+", "", s)

    lines = s.split("\n")
    out = []
    in_ol = in_ul = False

    def close_lists():
        nonlocal in_ol, in_ul
        if in_ol:
            out.append("</ol>")
            in_ol = False
        if in_ul:
            out.append("</ul>")
            in_ul = False

    for raw in lines:
        line = raw.strip()
        if re.match(r"^\d+\.\s+", line):  # ordered list
            if not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            out.append("<li>" + re.sub(r"^\d+\.\s+", "", line) + "</li>")
        elif re.match(r"^[-*]\s+", line):  # unordered list
            if not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + re.sub(r"^[-*]\s+", "", line) + "</li>")
        elif line == "":
            close_lists()
            out.append("<br>")
        else:
            close_lists()
            out.append("<p>" + line + "</p>")

    close_lists()
    return "<div class='md'>" + "\n".join(out) + "</div>"

# -----------------------------
# Routes: core app
# -----------------------------
@app.route('/')
def home():
    tickets = load_tickets()
    total_tickets = len(tickets)
    open_tickets = sum(1 for t in tickets if t['status'] == 'Open')
    closed_tickets = sum(1 for t in tickets if t['status'] == 'Closed')
    high_priority = sum(1 for t in tickets if t.get('priority') == 'High')
    medium_priority = sum(1 for t in tickets if t.get('priority') == 'Medium')
    low_priority = sum(1 for t in tickets if t.get('priority') == 'Low')
    session['open_count'] = open_tickets

    return render_template(
        'index.html',
        total=total_tickets,
        open_count=open_tickets,
        closed_count=closed_tickets,
        high=high_priority,
        medium=medium_priority,
        low=low_priority
    )

@app.route('/tickets', methods=['GET', 'POST'])
def tickets():
    tickets = load_tickets()

    if request.method == 'POST':
        issue_text = request.form['issue'].lower()

        if any(word in issue_text for word in ['network', 'down', 'email', 'outage']):
            priority = 'High'
        elif any(word in issue_text for word in ['printer', 'software', 'slow', 'password']):
            priority = 'Medium'
        else:
            priority = 'Low'

        new_ticket = {
            "id": str(uuid.uuid4())[:8],
            "name": request.form['name'],
            "issue": request.form['issue'],
            "status": "Open",
            "created": datetime.now().strftime('%Y-%m-%d %H:%M'),
            "priority": priority,
            "assigned": "Unassigned"
        }

        tickets.append(new_ticket)
        save_tickets(tickets)
        return redirect(url_for('tickets', success='1'))

    # Handle search
    query = request.args.get('search', '').lower()
    success = request.args.get('success')

    if query:
        tickets = [
            t for t in tickets
            if query in t['name'].lower()
            or query in t['issue'].lower()
            or query in t['status'].lower()
            or query in t.get('priority', '').lower()
            or query in t.get('assigned', '').lower()
        ]

    return render_template('tickets.html', tickets=tickets, search=query, success=success)

@app.route('/tickets/close/<ticket_id>')
def close_ticket(ticket_id):
    tickets = load_tickets()
    for t in tickets:
        if t['id'] == ticket_id:
            t['status'] = 'Closed'
    save_tickets(tickets)
    return redirect(url_for('tickets'))

@app.route('/tickets/edit/<ticket_id>', methods=['GET', 'POST'])
def edit_ticket(ticket_id):
    tickets = load_tickets()
    ticket = next((t for t in tickets if t['id'] == ticket_id), None)
    if not ticket:
        return "Ticket not found", 404

    fake_users = ['Alex Smith', 'Jamie Lee', 'Taylor Brown', 'Jordan Rivera']

    if request.method == 'POST':
        ticket['priority'] = request.form['priority']
        ticket['issue'] = request.form['issue']
        ticket['assigned'] = request.form['assigned']
        ticket['status'] = request.form['status']
        save_tickets(tickets)
        return redirect(url_for('tickets'))

    return render_template('edit_ticket.html', ticket=ticket, users=fake_users)

from flask import abort
@app.route('/azure')
def azure():
    abort(410)  # Gone

# -----------------------------
# AI providers
# -----------------------------
def call_groq(messages):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None, "GROQ_API_KEY missing. Add it to your .env."
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    max_tokens = int(os.getenv("MAX_TOKENS", "512"))
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": max_tokens
            },
            timeout=45
        )
        if resp.status_code != 200:
            return None, f"Groq error: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return strip_think_tags(content), None
    except Exception as e:
        return None, f"Groq exception: {e}"

def call_openrouter(messages, model=None):
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None, "OpenRouter API key missing. Set OPENROUTER_API_KEY (or switch AI_PROVIDER=groq)."
    model = model or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat-v3-0324:free")
    max_tokens = int(os.getenv("MAX_TOKENS", "512"))
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://example.com",
                "X-Title": "Helpie AI Troubleshooter"
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": max_tokens
            },
            timeout=45
        )
        if resp.status_code != 200:
            return None, f"OpenRouter error: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return strip_think_tags(content), None
    except Exception as e:
        return None, f"OpenRouter exception: {e}"

def call_azure_openai(messages):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not (endpoint and api_key and deployment):
        return None, "Azure OpenAI env vars missing. Set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT."
    try:
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version=2024-02-15-preview"
        max_tokens = int(os.getenv("MAX_TOKENS", "512"))
        resp = requests.post(
            url,
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": max_tokens
            },
            timeout=45
        )
        if resp.status_code != 200:
            return None, f"Azure OpenAI error: {resp.status_code} {resp.text[:200]}"
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return strip_think_tags(content), None
    except Exception as e:
        return None, f"Azure OpenAI exception: {e}"

# -----------------------------
# Helpie (AI Troubleshooter) - Chat UI
# -----------------------------
HELP_PROMPT = """You are Helpie, a friendly IT helpdesk AI for non-technical employees.
Your only job is troubleshooting devices, apps, accounts, and networks.

STYLE (Markdown only):
- Start with a short, varied reassurance sentence with a fitting emoji (e.g., 🔌 power, 📶 network, 🔐 login, 🖨️ printer, 🧰 general).
- Then an ordered list (1–6), one step per line; be concrete; name the app/OS when useful; emojis OK but not spam.
- End with **Escalate if:** and 1–3 brief conditions.
- Do not leave dangling markdown like stray ** anywhere.

SCOPE CONTROL:
- If the user asks for non-IT content, briefly say you're for IT help only and ask for an IT issue instead.
"""

def get_chat_history():
    return session.get("helpie_chat", [])

def set_chat_history(history):
    session["helpie_chat"] = history

def seed_chat():
    """Always start fresh when /helpie is loaded."""
    seed_text = "Hi! I’m Helpie 🤖. Tell me what’s broken and I’ll walk you through quick steps."
    seed_html = "Hi! I’m <strong>Helpie</strong> 🤖. Tell me what’s broken and I’ll walk you through quick steps."
    set_chat_history([{"role": "assistant", "content": seed_text, "html": f"<div class='md'><p>{seed_html}</p></div>"}])

@app.route('/helpie', methods=['GET'])
def helpie():
    # Fresh conversation on every page load
    seed_chat()

    # Inline template for the chat UI
    return render_template_string("""
    {% extends "base.html" %}
    {% block content %}
    <div class="helpie-wrap">
      <div class="helpie-header">
        <div class="title">🤖 Helpie — AI Troubleshooter</div>
        <a class="btn btn-sm btn-outline-light" href="/helpie" title="Clear conversation">Reset</a>
      </div>

      <div id="chatLog" class="chat-log">
        {% for m in session.get('helpie_chat', []) %}
          {% if m.role == 'assistant' %}
            <div class="msg bot"><div class="bubble">🤖 {% if m.html %}{{ m.html|safe }}{% else %}{{ m.content|safe }}{% endif %}</div></div>
          {% else %}
            <div class="msg user"><div class="bubble">{{ m.content|e }}</div></div>
          {% endif %}
        {% endfor %}
      </div>

      <div class="input-area">
        <textarea id="chatInput" class="form-control chat-input" rows="1" placeholder="Type your issue and hit Enter… (Shift+Enter for a new line)"></textarea>
      </div>
    </div>

    <script>
    const chatLog = document.getElementById('chatLog');
    const chatInput = document.getElementById('chatInput');

    let userMsgCount = 0;

    function scrollToBottom() { chatLog.scrollTop = chatLog.scrollHeight; }
    scrollToBottom();

    function appendMessage(role, text) {
      const wrapper = document.createElement('div');
      wrapper.className = 'msg ' + (role === 'assistant' ? 'bot' : 'user');
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      if (role === 'assistant') {
        bubble.innerHTML = '🤖 ' + text; // text is HTML from server
      } else {
        const prefix = (userMsgCount === 0 ? '🙋‍♂️ ' : '');
        const div = document.createElement('div');
        div.textContent = text; // escape user text
        bubble.innerHTML = prefix + div.innerHTML;
        userMsgCount++;
      }
      wrapper.appendChild(bubble);
      chatLog.appendChild(wrapper);
      scrollToBottom();
    }

    function showTyping() {
      const w = document.createElement('div');
      w.className = 'msg bot typing';
      w.id = 'typing';
      w.innerHTML = '<div class="bubble"><span class="dots"><i></i><i></i><i></i></span></div>';
      chatLog.appendChild(w);
      scrollToBottom();
    }
    function hideTyping() {
      const t = document.getElementById('typing');
      if (t) t.remove();
    }

    async function sendMessage(text) {
      appendMessage('user', text);
      showTyping();
      chatInput.value = '';
      chatInput.disabled = true;

      try {
        const res = await fetch('/helpie/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ message: text })
        });
        const data = await res.json();
        hideTyping();
        if (data.error) {
          appendMessage('assistant', '⚠️ ' + data.error + '<br><small>Set GROQ_API_KEY in .env (or Azure/OpenRouter vars).</small>');
        } else {
          appendMessage('assistant', data.reply); // reply is HTML
        }
      } catch (e) {
        hideTyping();
        appendMessage('assistant', '⚠️ Network error. Please try again.');
      } finally {
        chatInput.disabled = false;
        chatInput.focus();
        scrollToBottom();
      }
    }

    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const val = chatInput.value.trim();
        if (val) sendMessage(val);
      }
    });
    </script>
    {% endblock %}
    """)  # noqa: E501

@app.route('/helpie/chat', methods=['POST'])
def helpie_chat():
    data = request.get_json(silent=True) or {}
    user_msg = (data.get('message') or '').strip()
    if not user_msg:
        return jsonify({"error": "Empty message."})

    # Build chat history for the model (use raw 'content', not HTML)
    history = get_chat_history()
    messages = [{"role": "system", "content": HELP_PROMPT}]
    for m in history[-15:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_msg})

    # Provider preference: Groq -> Azure -> OpenRouter
    reply, err = (None, None)
    if os.getenv("GROQ_API_KEY"):
        reply, err = call_groq(messages)
    elif os.getenv("AZURE_OPENAI_ENDPOINT") and os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_DEPLOYMENT"):
        reply, err = call_azure_openai(messages)
    else:
        reply, err = call_openrouter(messages)

    if err:
        return jsonify({"error": err})

    reply_html = md_to_html(reply)

    # Persist to session (store both raw + html)
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": reply, "html": reply_html})
    set_chat_history(history)

    return jsonify({"reply": reply_html})

# -----------------------------

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
