// ── DOM refs ──
const chat = document.getElementById('chat');
const form = document.getElementById('f');
const input = document.getElementById('q');
const button = document.getElementById('send');
const personaSelect = document.getElementById('persona');
const chatList = document.getElementById('chat-list');
const newChatBtn = document.getElementById('new-chat-btn');
const personaLabels = {};

// ── Personas ──
async function loadPersonas() {
  try {
    const res = await fetch('/personas');
    const data = await res.json();
    personaSelect.innerHTML = '';
    for (const p of data.items) {
      personaLabels[p.key] = p.label;
      const opt = document.createElement('option');
      opt.value = p.key;
      opt.textContent = p.label;
      if (p.key === data.default) opt.selected = true;
      personaSelect.appendChild(opt);
    }
  } catch (e) { console.error('Could not load personas', e); }
}
loadPersonas();

// ── Chat storage (localStorage) ──
const STORE = 'chatnik_chats';

function loadAllChats() {
  return JSON.parse(localStorage.getItem(STORE) || '[]');
}

function saveAllChats(chats) {
  localStorage.setItem(STORE, JSON.stringify(chats));
}

// ── Current state ──
let currentId = null;           // active chat id
let currentMessages = [];       // [{role, text, persona}]
let pendingHistory = null;      // history to inject on next send (when resuming)

// ── Sidebar ──
function renderSidebar() {
  const chats = loadAllChats();
  chatList.innerHTML = '';
  for (const c of [...chats].reverse()) {
    const item = document.createElement('div');
    item.className = 'chat-item' + (c.id === currentId ? ' active' : '');
    item.dataset.id = c.id;

    const title = document.createElement('span');
    title.className = 'chat-item-title';
    title.textContent = c.title || 'New chat';

    const del = document.createElement('button');
    del.className = 'chat-item-del';
    del.textContent = '✕';
    del.title = 'Delete';
    del.onclick = (e) => { e.stopPropagation(); deleteChat(c.id); };

    item.appendChild(title);
    item.appendChild(del);
    item.onclick = () => loadChat(c.id);
    chatList.appendChild(item);
  }
}

function saveCurrentChat() {
  if (!currentId || currentMessages.length === 0) return;
  const chats = loadAllChats();
  const idx = chats.findIndex(c => c.id === currentId);
  const title = currentMessages[0]?.text?.slice(0, 48) || 'New chat';
  if (idx >= 0) {
    chats[idx].messages = currentMessages;
    chats[idx].title = title;
  } else {
    chats.push({ id: currentId, title, messages: currentMessages, createdAt: Date.now() });
  }
  saveAllChats(chats);
}

function deleteChat(id) {
  const chats = loadAllChats().filter(c => c.id !== id);
  saveAllChats(chats);
  if (id === currentId) startNewChat();
  else renderSidebar();
}

// ── New chat ──
async function startNewChat() {
  saveCurrentChat();
  currentId = crypto.randomUUID();
  currentMessages = [];
  pendingHistory = null;
  clearChatUI();
  renderSidebar();
  input.focus();
  try { await fetch('/new-chat', { method: 'POST' }); } catch(e) {}
}

newChatBtn.onclick = startNewChat;

personaSelect.addEventListener('change', () => {
  if (currentMessages.length > 0) pendingHistory = [...currentMessages];
  try { fetch('/new-chat', { method: 'POST' }); } catch(e) {}
});

// ── Load old chat ──
function loadChat(id) {
  if (id === currentId) return;
  saveCurrentChat();

  const chats = loadAllChats();
  const c = chats.find(ch => ch.id === id);
  if (!c) return;

  currentId = id;
  currentMessages = c.messages || [];
  pendingHistory = currentMessages.length > 0 ? [...currentMessages] : null;

  clearChatUI();
  for (const m of currentMessages) {
    if (m.role === 'user') renderUserMsg(m.text);
    else renderBotMsg(m.text, m.persona, null);
  }
  renderSidebar();
  input.focus();
}

// ── UI helpers ──
let emptyEl = document.getElementById('empty');

function clearChatUI() {
  chat.innerHTML = '';
  emptyEl = document.createElement('div');
  emptyEl.className = 'empty-state';
  emptyEl.id = 'empty';
  emptyEl.innerHTML = `
    <img src="/static/chatnik.png" class="empty-icon" alt="Chatnik">
    <p class="empty-title">What can I help you with?</p>
    <p class="empty-sub">Ask anything — switch personas for different flavors of answers.</p>
    <div class="suggestions">
      <button class="chip" type="button" onclick="fillPrompt(this)">What is Chatnik?</button>
      <button class="chip" type="button" onclick="fillPrompt(this)">Write a bubble sort in C</button>
      <button class="chip" type="button" onclick="fillPrompt(this)">Explain async/await simply</button>
      <button class="chip" type="button" onclick="fillPrompt(this)">Tell me a programming joke</button>
    </div>`;
  chat.appendChild(emptyEl);
}

function removeEmpty() {
  if (emptyEl) { emptyEl.remove(); emptyEl = null; }
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function fillPrompt(btn) {
  input.value = btn.textContent;
  input.focus();
}

function renderUserMsg(text) {
  removeEmpty();
  const g = document.createElement('div');
  g.className = 'msg-group user';
  g.innerHTML = `<div class="sender">You</div><div class="msg">${esc(text)}</div>`;
  chat.appendChild(g);
  chat.scrollTop = chat.scrollHeight;
}

function renderBotMsg(text, personaKey, elapsedS) {
  removeEmpty();
  const label = personaLabels[personaKey] || personaKey || 'bot';
  const g = document.createElement('div');
  g.className = 'msg-group bot';

  const sender = document.createElement('div');
  sender.className = 'sender';
  sender.textContent = label;

  const msg = document.createElement('div');
  msg.className = 'msg';
  msg.innerHTML = marked.parse(text);

  g.appendChild(sender);
  g.appendChild(msg);

  if (elapsedS != null) {
    const t = document.createElement('div');
    t.className = 'thought-time';
    t.textContent = `Thought for ${elapsedS}s`;
    g.appendChild(t);
  }

  chat.appendChild(g);
  chat.scrollTop = chat.scrollHeight;
}

function renderErrMsg(text) {
  removeEmpty();
  const g = document.createElement('div');
  g.className = 'msg-group bot';
  g.innerHTML = `<div class="sender">Error</div><div class="msg err">${esc(text)}</div>`;
  chat.appendChild(g);
  chat.scrollTop = chat.scrollHeight;
}

// ── Thinking animation ──
const THINK_WORDS = [
  'Thinking','Pondering','Mulling','Cogitating','Brewing',
  'Deliberating','Ruminating','Recombobulating','Befuddling',
  'Synthesizing','Marinating','Percolating','Crystallizing',
  'Conjuring','Coalescing','Wibbling','Schlepping','Noodling',
  'Sussing','Distilling','Reckoning','Untangling','Concocting',
];
const GLYPHS = ['✻','✶','✷','✸','✹','✺','✽'];

function startThinking() {
  removeEmpty();
  const wrap = document.createElement('div');
  wrap.className = 'msg-group bot thinking-wrap';
  const senderEl = document.createElement('div');
  senderEl.className = 'sender';
  senderEl.textContent = ' ';
  const div = document.createElement('div');
  div.className = 'thinking';
  div.innerHTML =
    '<span class="glyph">✻</span>' +
    '<span class="thoughts"><span class="word"></span><span class="dots"></span></span>' +
    '<span class="dim">(<span class="t">0</span>s)</span>';
  wrap.appendChild(senderEl);
  wrap.appendChild(div);
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;

  const glyph = div.querySelector('.glyph');
  const word  = div.querySelector('.word');
  const dots  = div.querySelector('.dots');
  const t     = div.querySelector('.t');
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  let stopped = false;
  const Date_now_start = Date.now();
  const ticker2 = setInterval(() => { t.textContent = Math.floor((Date.now() - Date_now_start) / 1000); }, 1000);

  (async () => {
    let lastWord = '';
    while (!stopped) {
      let w = lastWord;
      while (w === lastWord) w = THINK_WORDS[Math.floor(Math.random() * THINK_WORDS.length)];
      lastWord = w;
      glyph.textContent = GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      word.textContent = '';
      dots.textContent = '';
      for (const ch of w) {
        if (stopped) return;
        word.textContent += ch;
        await sleep(55 + Math.random() * 45);
      }
      for (let i = 0; i < 6 && !stopped; i++) {
        dots.textContent = '.'.repeat((i % 3) + 1);
        await sleep(380);
      }
    }
  })();

  return {
    node: wrap,
    stop: () => { stopped = true; clearInterval(ticker2); }
  };
}

// ── Submit ──
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const prompt = input.value.trim();
  if (!prompt) return;

  if (!currentId) { currentId = crypto.randomUUID(); }

  renderUserMsg(prompt);
  currentMessages.push({ role: 'user', text: prompt });

  input.value = '';
  input.disabled = true;
  button.disabled = true;

  const thinking = startThinking();
  const start = Date.now();

  // Inject history only on first message of a resumed chat
  const history = pendingHistory;
  pendingHistory = null;

  try {
    const res = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, persona: personaSelect.value, history }),
    });
    const data = await res.json();
    const elapsed = ((Date.now() - start) / 1000).toFixed(1);
    thinking.stop();
    thinking.node.remove();
    if (res.ok) {
      renderBotMsg(data.answer, data.persona, elapsed);
      currentMessages.push({ role: 'bot', text: data.answer, persona: data.persona });
      saveCurrentChat();
      renderSidebar();
    } else {
      renderErrMsg('Error: ' + (data.detail || res.statusText));
    }
  } catch (err) {
    thinking.stop();
    thinking.node.remove();
    renderErrMsg('Error: ' + err.message);
  } finally {
    input.disabled = false;
    button.disabled = false;
    input.focus();
  }
});

// ── Init ──
currentId = crypto.randomUUID();
renderSidebar();
