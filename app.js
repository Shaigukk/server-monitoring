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

const WORDS = { ok: "норма", warn: "близко", alert: "выше порога", unknown: "нет данных" };

let logText = "";
let editing = false;
const cards = new Map();

function number(value) {
  return value === null || value === undefined ? "н/д" : String(value);
}

function level(value, limit) {
  if (value === null || value === undefined || !limit) return "unknown";
  if (value > limit) return "alert";
  if (value >= limit * 0.8) return "warn";
  return "ok";
}

function ensureCard(key) {
  let node = cards.get(key);
  if (node) return node;
  node = document.createElement("article");
  node.className = "card";
  node.dataset.key = key;
  node.innerHTML =
    '<div class="card-top"><p class="card-label"></p><span class="card-state"></span></div>' +
    '<p class="card-value"><span class="num"></span><small>%</small></p>' +
    '<div class="bar"><i></i><b hidden></b></div>' +
    '<p class="card-meta"></p>';
  cards.set(key, node);
  return node;
}

function paint(key, label, value, meta, limit) {
  const node = ensureCard(key);
  const state = level(value, limit);
  const known = state !== "unknown";
  node.dataset.level = state;
  node.querySelector(".card-label").textContent = label;
  node.querySelector(".card-state").textContent = WORDS[state];
  const figure = node.querySelector(".card-value");
  figure.classList.toggle("is-empty", !known);
  figure.querySelector(".num").textContent = known ? number(value) : "н/д";
  figure.querySelector("small").hidden = !known;
  node.querySelector(".bar i").style.width = (known ? Math.max(0, Math.min(100, value)) : 0) + "%";
  const tick = node.querySelector(".bar b");
  if (limit) {
    tick.hidden = false;
    tick.style.left = Math.max(0, Math.min(100, limit)) + "%";
  } else {
    tick.hidden = true;
  }
  node.querySelector(".card-meta").textContent = meta;
  return node;
}

function render(data) {
  const limits = data.thresholds || {};
  host.textContent = data.hostname || "Этот компьютер";
  taken.textContent = data.taken_at
    ? "Опрос " + data.taken_at + ", каждые " + (data.poll_seconds || "н/д") + " с"
    : "Показатели ещё не собраны";

  const memory = data.memory || {};
  const rows = [
    ["cpu", "Процессор", data.cpu, "порог " + number(limits.cpu) + "%", limits.cpu],
    [
      "memory",
      "Память",
      memory.percent,
      memory.percent == null
        ? "не прочитана"
        : "занято " + memory.used_gb + " ГБ, свободно " + memory.free_gb + " ГБ",
      limits.memory,
    ],
  ];
  (data.disks || []).forEach(function (disk) {
    const meta = disk.percent == null
      ? "не прочитан"
      : "занято " + disk.used_gb + " ГБ, свободно " + disk.free_gb + " ГБ";
    rows.push(["disk-" + disk.letter, "Диск " + disk.letter, disk.percent, meta, limits.disk]);
  });

  const alive = new Set(rows.map(function (row) { return row[0]; }));
  cards.forEach(function (node, key) {
    if (!alive.has(key)) {
      node.remove();
      cards.delete(key);
    }
  });
  metrics.replaceChildren.apply(metrics, rows.map(function (row) {
    return paint(row[0], row[1], row[2], row[3], row[4]);
  }));
  metrics.removeAttribute("aria-busy");

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
  banner.hidden = ok || !cards.size;
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
  note.dataset.bad = "0";
  note.textContent = "Сохраняю.";
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
    note.dataset.bad = "1";
    note.textContent = error.message;
  }
});

refresh();
setInterval(refresh, 2000);
