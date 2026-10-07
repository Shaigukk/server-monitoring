const metrics = document.querySelector("#metrics");
const alerts = document.querySelector("#alerts");
const alertCount = document.querySelector("#alert-count");
const logBox = document.querySelector("#log");
const banner = document.querySelector("#banner");
const host = document.querySelector("#host");
const taken = document.querySelector("#taken");
const link = document.querySelector("#link");
const form = document.querySelector("#settings");
const note = document.querySelector("#settings-note");
const fields = {
  cpu: document.querySelector("#cpu-limit"),
  memory: document.querySelector("#memory-limit"),
  disk: document.querySelector("#disk-limit"),
};

let logText = "";
let editing = false;

function number(value) {
  return value === null || value === undefined ? "—" : String(value);
}

function level(value, limit) {
  if (value === null || value === undefined || !limit) return "unknown";
  if (value > limit) return "alert";
  if (value >= limit * 0.8) return "warn";
  return "ok";
}

function card(label, value, meta, limit) {
  const node = document.createElement("article");
  const state = level(value, limit);
  node.className = "card";
  node.dataset.level = state;
  const width = state === "unknown" ? 0 : Math.max(0, Math.min(100, value));
  node.innerHTML =
    '<p class="card-label"></p>' +
    '<p class="card-value"></p>' +
    '<div class="bar"><i></i></div>' +
    '<p class="card-meta"></p>';
  node.querySelector(".card-label").textContent = label;
  node.querySelector(".card-value").innerHTML = state === "unknown"
    ? "\u2014"
    : number(value) + "<small>%</small>";
  node.querySelector(".bar i").style.width = width + "%";
  node.querySelector(".card-meta").textContent = meta;
  return node;
}

function render(data) {
  const limits = data.thresholds || {};
  host.textContent = data.hostname || "Этот компьютер";
  taken.textContent = data.taken_at
    ? "Опрос " + data.taken_at + " · каждые " + (data.poll_seconds || "—") + " с"
    : "Показатели ещё не собраны";

  const memory = data.memory || {};
  const nodes = [
    card(
      "Процессор",
      data.cpu,
      "порог " + number(limits.cpu) + "%",
      limits.cpu
    ),
    card(
      "Память",
      memory.percent,
      memory.percent == null
        ? "не прочитана"
        : "занято " + memory.used_gb + " ГБ · свободно " + memory.free_gb + " ГБ",
      limits.memory
    ),
  ];
  (data.disks || []).forEach(function (disk) {
    const meta = disk.percent == null
      ? "не прочитан"
      : "занято " + disk.used_gb + " ГБ · свободно " + disk.free_gb + " ГБ";
    nodes.push(card("Диск " + disk.letter, disk.percent, meta, limits.disk));
  });
  metrics.replaceChildren.apply(metrics, nodes);

  const items = data.alerts || [];
  alertCount.textContent = String(items.length);
  alertCount.dataset.hot = items.length ? "1" : "0";
  alerts.replaceChildren();
  if (!items.length) {
    const quiet = document.createElement("p");
    quiet.className = "quiet";
    quiet.textContent = "Пороги не превышены.";
    alerts.appendChild(quiet);
  } else {
    items.forEach(function (item) {
      const row = document.createElement("p");
      row.className = "alert";
      row.textContent = item.text;
      alerts.appendChild(row);
    });
  }

  const nextLog = (data.log || []).join("\n") || "Журнал пуст.";
  if (nextLog !== logText) {
    const pinned = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 24;
    logBox.textContent = nextLog;
    logText = nextLog;
    if (pinned) logBox.scrollTop = logBox.scrollHeight;
  }

  if (!editing) {
    ["cpu", "memory", "disk"].forEach(function (key) {
      if (limits[key] != null && document.activeElement !== fields[key]) {
        fields[key].value = limits[key];
      }
    });
  }
}

function mark(ok) {
  link.dataset.state = ok ? "ok" : "lost";
  link.lastChild.textContent = ok ? "на связи" : "нет связи";
  banner.hidden = ok || metrics.children.length === 0;
}

async function refresh() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    if (data.ready === false) {
      mark(true);
      return;
    }
    render(data);
    mark(true);
  } catch (error) {
    mark(false);
  }
}

form.addEventListener("focusin", function () { editing = true; });
form.addEventListener("focusout", function () {
  setTimeout(function () {
    if (!form.contains(document.activeElement)) editing = false;
  }, 0);
});

form.addEventListener("submit", async function (event) {
  event.preventDefault();
  note.textContent = "Сохраняю…";
  const body = {
    cpu: Number(fields.cpu.value),
    memory: Number(fields.memory.value),
    disk: Number(fields.disk.value),
  };
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "не сохранено");
    note.textContent = "Пороги сохранены и уже применяются.";
    editing = false;
    refresh();
  } catch (error) {
    note.textContent = error.message;
  }
});

refresh();
setInterval(refresh, 2000);
