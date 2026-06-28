const messagesEl = document.querySelector("#messages");
const formEl = document.querySelector("#composer");
const inputEl = document.querySelector("#input");
const sendEl = document.querySelector("#send");
const statusEl = document.querySelector("#status");

const state = {
  messages: [],
  busy: false,
};

function render() {
  messagesEl.innerHTML = "";
  if (state.messages.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "连接本机 QRClaw，输入任务后开始。";
    messagesEl.appendChild(empty);
    return;
  }

  for (const message of state.messages) {
    const item = document.createElement("article");
    item.className = `msg ${message.role}`;

    const role = document.createElement("div");
    role.className = "role";
    role.textContent = message.role === "user" ? "你" : "QRClaw";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = message.content;

    item.append(role, bubble);
    messagesEl.appendChild(item);
  }
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function checkHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error("bad health");
    statusEl.textContent = "已连接";
    statusEl.className = "status ok";
  } catch {
    statusEl.textContent = "未连接";
    statusEl.className = "status err";
  }
}

async function sendMessage(content) {
  state.busy = true;
  sendEl.disabled = true;
  sendEl.textContent = "运行中";
  state.messages.push({ role: "user", content });
  render();

  try {
    const response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "qrclaw-agent",
        messages: state.messages.map((message) => ({
          role: message.role,
          content: message.content,
        })),
      }),
    });

    const body = await response.json();
    if (!response.ok) {
      throw new Error(body.detail || response.statusText);
    }

    const reply = body.choices?.[0]?.message?.content || "";
    state.messages.push({ role: "assistant", content: reply });
  } catch (error) {
    state.messages.push({
      role: "assistant",
      content: `调用失败：${error.message || error}`,
    });
  } finally {
    state.busy = false;
    sendEl.disabled = false;
    sendEl.textContent = "发送";
    render();
  }
}

formEl.addEventListener("submit", (event) => {
  event.preventDefault();
  if (state.busy) return;
  const content = inputEl.value.trim();
  if (!content) return;
  inputEl.value = "";
  sendMessage(content);
});

inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    formEl.requestSubmit();
  }
});

checkHealth();
render();

