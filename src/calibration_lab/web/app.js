"use strict";
let state = {}, currentView = "overview", signature = "", jobSignature = "";
const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const number = (v, digits = 0) => typeof v === "number" ? v.toLocaleString("ru-RU", {minimumFractionDigits:digits,maximumFractionDigits:digits}) : "—";
const fmt = v => number(v, 4);
const modelNames = {uncalibrated:"LogisticRegression",calibrated:"После калибровки",naive:"Наивный ориентир"};
const plotPath = terms => {
  const plots = state.results?.plots || {};
  const key = Object.keys(plots).find(k => terms.some(t => k.toLowerCase().includes(t)));
  if (!key) return null;
  const value = typeof plots[key] === "object" ? plots[key].path : plots[key];
  return {key, url:"/" + String(value).replace(/^\//, "")};
};
const chart = (terms, large = false) => {
  const p = plotPath(terms);
  return p ? `<img class="chart ${large ? "chart-large" : "chart-preview"}" src="${esc(p.url)}?run=${esc(state.results?.run_id)}" alt="График ${esc(p.key)}">` : '<p class="small">График появится после запуска анализа.</p>';
};
const kpi = (title,value,foot,teal=false) => `<div class="card"><div class="kpi-label">${title}</div><div class="kpi-value ${teal ? "teal" : ""}">${value}</div><div class="kpi-foot">${foot}</div></div>`;
const cards = () => {
  const s = state.results?.summary || {};
  return `<div class="cards">${kpi("НАБЛЮДЕНИЯ",number(s.rows),"открытый набор данных WDBC")}${kpi("ЧИСЛОВЫЕ ПРИЗНАКИ",number(s.features),"характеристики клеточных ядер")}${kpi("ПРОПУСКИ",number(s.missing_values),"проверено средствами pandas",true)}${kpi("TEST · BRIER SCORE",fmt(state.results?.metrics?.uncalibrated?.brier),"исходная модель · меньше лучше",true)}</div>`;
};
function metricTable(full=false) {
  const metrics = state.results?.metrics || {};
  const cols = full ? ["brier","log_loss","ece","accuracy","roc_auc"] : ["brier","ece"];
  const headers = {brier:"Brier ↓",log_loss:"Log loss ↓",ece:"ECE ↓",accuracy:"Accuracy ↑",roc_auc:"ROC-AUC ↑"};
  return `<table class="mini-table"><thead><tr><th>Модель</th>${cols.map(c=>`<th>${headers[c]}</th>`).join("")}</tr></thead><tbody>${["uncalibrated","calibrated","naive"].map(name=>`<tr class="${name==="calibrated" ? "highlight" : ""}"><td>${modelNames[name]}</td>${cols.map(c=>`<td>${fmt(metrics[name]?.[c])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}
function splitPanel() {
  const r = state.results || {}, s = r.splits || {};
  const val = x => typeof x === "object" ? (x?.rows ?? x?.n ?? x?.size) : x;
  return `<h2>Эксперимент без утечки</h2><p class="sub">Три независимые части данных</p><div class="steps"><div class="step"><strong>${number(val(s.train))}</strong><span>TRAIN · обучение</span></div><div class="step"><strong>${number(val(s.calibration))}</strong><span>CAL · калибровка</span></div><div class="step"><strong>${number(val(s.test))}</strong><span>TEST · проверка</span></div></div><div class="meta-row"><span>Зерно случайности</span><code>${esc(r.seed ?? 42)}</code></div><div class="meta-row"><span>Базовая модель</span><b>LogisticRegression</b></div><div class="meta-row"><span>Калибровка</span><b>Sigmoid · FrozenEstimator</b></div><div class="meta-row"><span>Положительное событие</span><b>Злокачественный класс</b></div><p class="summary-note">Scaler и модель обучаются только на train. Калибратор использует отдельную calibration-часть. Test не участвует в обучении.</p>`;
}
function calibrationNote() {
  const m=state.results?.metrics || {}, a=m.uncalibrated?.brier, b=m.calibrated?.brier;
  if (typeof a!=="number" || typeof b!=="number") return "Результаты появятся после анализа.";
  return b < a ? `На этом разбиении Brier снизился с ${fmt(a)} до ${fmt(b)}. Это результат одного фиксированного эксперимента, а не гарантия улучшения на новых данных.` : `На этом разбиении Brier изменился с ${fmt(a)} до ${fmt(b)}: калибровка не улучшила эту метрику. Исходная логистическая регрессия уже даёт сильный результат.`;
}
function overview() {
  return cards()+`<div class="grid"><section class="panel"><div class="panel-head"><h2>Соответствуют ли вероятности частотам?</h2><span class="tag">ОТЛОЖЕННЫЙ TEST</span></div><p class="sub">Калибровочная диаграмма до и после преобразования</p>${chart(["calibration","reliability"])}<p class="chart-caption">Близость к диагонали показывает согласованность вероятностей и наблюдаемых долей.</p></section><section class="panel">${splitPanel()}</section></div><div class="grid"><section class="panel"><div class="panel-head"><h2>Измеренное качество вероятностей</h2><span class="tag">МЕНЬШЕ — ЛУЧШЕ</span></div>${metricTable()}<p class="summary-note">${calibrationNote()}</p></section><section class="panel"><h2>Проверяемый результат</h2><p class="sub">Исходные данные, запуск и контроль изменений</p><div class="meta-row"><span>Данные</span><b>SHA-256 · фиксированный эталон</b></div><div class="meta-row"><span>Эксперименты</span><b>Локальный MLflow</b></div><div class="meta-row"><span>Git</span><b>${esc(state.git?.branch || "см. Git bundle")}</b></div><p class="plain-note">Статус удалённого GitHub CI и защиты main проверяется после публикации в аккаунте владельца.</p><button class="secondary" data-view="checks">Открыть проверки →</button></section></div>`;
}
function calibration() {
  return `<div class="panel" style="margin-bottom:20px"><div class="panel-head"><h2>Вероятность события и наблюдаемая доля</h2><span class="tag">SIGMOID CALIBRATION</span></div><p class="sub">Событие — злокачественный класс. На диаграмме используются только отложенные наблюдения.</p>${chart(["calibration","reliability"],true)}</div><div class="grid"><section class="panel"><h2>Сравнение на одном test</h2>${metricTable(true)}<p class="summary-note">${calibrationNote()}</p><p class="small">Brier и log loss отражают не только калибровку. ECE зависит от разбиения на интервалы; на малом test отдельные точки ненадёжны.</p></section><section class="panel"><h2>Как читать диаграмму</h2><p class="summary-note">По горизонтали — средняя вероятность модели в интервале, по вертикали — фактическая доля положительного класса в том же интервале.</p><p class="summary-note">Идеально калиброванная модель располагается на диагонали. Высокая accuracy означает правильные классы, но не гарантирует правильные вероятности.</p><p class="summary-note">Количество наблюдений в интервалах сохранено в таблице калибровки. Все метрики связаны с MLflow run.</p></section></div>`;
}
function dataView() {
  const s=state.results?.summary || {};
  return cards()+`<div class="grid"><section class="panel"><h2>Доли классов</h2>${chart(["class","balance"])}<p class="chart-caption">Сохраняем обе группы. Разбиение выполняется со стратификацией.</p></section><section class="panel"><h2>Контроль качества данных</h2><div class="meta-row"><span>Пропуски</span><b>${number(s.missing_values)}</b></div><div class="meta-row"><span>Полные дубликаты</span><b>${number(s.duplicate_rows)}</b></div><div class="meta-row"><span>Отрицательные значения</span><b>${number(s.negative_values)}</b></div><div class="meta-row"><span>Ячейки за границами IQR</span><b>${number(s.outlier_cells)}</b></div><div class="meta-row"><span>Строки с IQR-флагом</span><b>${number(s.outlier_rows)}</b></div><p class="summary-note">IQR-флаги обозначают необычные значения, а не доказанные ошибки. Наблюдения не удаляются автоматически.</p><p class="summary-note">Все числовые сводки доступны в <a href="/reports/LAB1/eda_stats.csv" target="_blank">eda_stats.csv</a>.</p></section></div><div class="data-grid"><section class="panel"><h2>Связи между признаками</h2>${chart(["correlation","corr"],true)}</section><section class="panel"><h2>Распределения признаков</h2>${chart(["distribution","hist"],true)}</section><section class="panel wide"><h2>Необычные значения</h2>${chart(["outlier","box"],true)}</section></div>`;
}
function checks() {
  const list=[
    ["01","Целостность данных","Сравнить размер и SHA-256 с фиксированным манифестом.","verify","Проверить SHA-256"],
    ["02","Контроль подмены","Изменить копию данных и убедиться, что проверка её отклоняет.","tamper","Проверить подмену"],
    ["03","Защита коммитов","Три реальные попытки коммита: стиль, фиктивный секрет, большой бинарный файл.","controls","Запустить блокировки"],
    ["04","Воспроизводимость","Два прогона с одним seed. Сравнение таблиц, разбиений и метрик.","reproduce","Повторить анализ"],
    ["05","Тесты проекта","Проверить данные, настройки, калибровку и защитные сценарии.","tests","Запустить тесты"],
  ];
  return `<div class="checks-grid">${list.map(([i,t,d,a,b])=>`<section class="check-card"><div class="check-number">ПРОВЕРКА ${i}</div><h3>${t}</h3><p>${d}</p><button class="secondary action" data-action="${a}">${b}</button></section>`).join("")}<section class="check-card"><div class="check-number">GITHUB</div><h3>Серверный контроль</h3><p>Конфигурация CI подготовлена. Фактический статус CI и защиты main подтверждается после публикации.</p><a class="secondary" href="/docs/git_workflow.md" target="_blank">Порядок публикации</a></section></div><div class="console-head"><h2>Журнал выполнения</h2><span class="console-status" id="console-status"></span></div><pre class="terminal" id="console">Выберите проверку. Здесь появится реальный вывод команды.</pre><div class="panel" style="margin-top:20px"><h2>Ветки и история изменений</h2><pre class="terminal" style="min-height:100px;max-height:270px">${esc(state.git?.graph || "В копии без .git историю можно восстановить из Git bundle. Инструкция — docs/git_workflow.md.")}</pre></div>`;
}
function files() {
  const labels={"lab1_report.md":"Основной отчёт","lab1_report.docx":"Отчёт Word","eda_stats.csv":"Сводки EDA","hash_manifest.json":"Манифест хешей","precommit_block.log":"Три блокировки коммитов","precommit_config.yaml":"Конфигурация pre-commit","results.json":"Результаты и MLflow run ID","reproducibility.json":"Повторный анализ","hash_tamper.log":"Обнаружение подмены","llm_review.md":"Рецензия ИИ","verification_summary.json":"Итог проверок"};
  const list=state.files||[];
  const r=state.results||{};
  return `<div class="grid"><section class="panel"><h2>Материалы лабораторной работы</h2><p class="sub">Сохранённые результаты текущего проекта</p><div class="files-list">${list.map(path=>{const base=path.split("/").pop();return `<a class="file" href="/${esc(path)}" target="_blank">${esc(labels[base]||base)}<small>${esc(base)}</small></a>`}).join("")}</div></section><section class="panel"><h2>Для защиты</h2><p class="sub">Пояснения и сценарий демонстрации</p><a class="file" href="/docs/defense_notes.md" target="_blank">Краткое объяснение проекта<small>Данные · EDA · калибровка · Git</small></a><br><a class="file" href="/docs/video_script.md" target="_blank">Сценарий видео<small>Пошаговая демонстрация на 3–5 минут</small></a><br><a class="file" href="/docs/git_workflow.md" target="_blank">Работа с GitHub<small>Ветки · pull request · защита main</small></a><p class="summary-note">ФИО, группа, ссылка на репозиторий и личный комментарий к рецензии заполняются владельцем перед сдачей.</p></section></div><div class="provenance"><strong>Происхождение результата</strong><br>Run ID: ${esc(r.run_id)}<br>Коммит кода: ${esc(r.git_commit)}<br>SHA-256 данных: ${esc(r.data?.sha256)}</div>`;
}
const pages={overview,calibration,data:dataView,checks,files};
const titles={overview:["Калибровка вероятностей","Данные, которым можно доверять. Вероятности, которые можно проверить."],calibration:["Качество вероятностей","Одна тестовая выборка. Три подхода. Измеренный результат."],data:["Разведочный анализ данных","Структура, распределения, взаимосвязи и необычные значения."],checks:["Проверки и воспроизводимость","Настоящие команды, проверяемые отказы и сохранённые журналы."],files:["Материалы для сдачи","Отчёт, таблицы, хеши и доказательства выполнения."]};
function render() {
  document.querySelectorAll(".nav").forEach(el=>el.classList.toggle("active",el.dataset.view===currentView));
  document.getElementById("page-title").textContent=titles[currentView][0];
  document.getElementById("page-subtitle").textContent=titles[currentView][1];
  document.getElementById("content").innerHTML=pages[currentView]();
  updateJob();
}
function updateJob() {
  const job=state.job||{}, pill=document.getElementById("status-pill");
  pill.classList.toggle("busy",!!job.running);
  pill.textContent=job.running?"Выполняется проверка…":state.results?.run_id?"Результаты доступны":"Готово к первому запуску";
  document.querySelectorAll("button.action").forEach(b=>{b.disabled=!!job.running});
  const consoleEl=document.getElementById("console");
  if(consoleEl && job.log){consoleEl.textContent=job.log; if(jobSignature!==job.log){consoleEl.scrollTop=consoleEl.scrollHeight; jobSignature=job.log;}}
  const cs=document.getElementById("console-status");
  if(cs)cs.textContent=job.running?"ВЫПОЛНЯЕТСЯ":job.exit_code===0?"ЗАВЕРШЕНО · КОД 0":job.exit_code?`ОШИБКА · КОД ${job.exit_code}`:"ОЖИДАНИЕ";
  const r=state.results||{};
  document.getElementById("run-meta").textContent=r.run_id?`MLflow run: ${r.run_id.slice(0,12)} · seed ${r.seed}`:"";
}
async function refresh() {
  try {
    const response=await fetch("/api/status");if(!response.ok)throw new Error("Сервер недоступен");
    state=await response.json();
    const next=JSON.stringify([state.results?.run_id,state.files,state.git?.commit]);
    if(next!==signature){signature=next;render();}else updateJob();
  } catch(error) { document.getElementById("status-pill").textContent="Нет связи с приложением"; }
}
async function run(action) {
  const response=await fetch("/api/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});
  const body=await response.json();
  if(!response.ok){const el=document.getElementById("notice");el.hidden=false;el.textContent=body.error;return;}
  document.getElementById("notice").hidden=true;
  currentView="checks";render();window.scrollTo(0,0);await refresh();
}
document.addEventListener("click",event=>{
  const nav=event.target.closest("[data-view]");if(nav){currentView=nav.dataset.view;render();window.scrollTo(0,0);}
  const button=event.target.closest("button[data-action]");if(button)run(button.dataset.action);
});
window.demoCaption=(step,title,body)=>{
  document.body.classList.add("recording");
  document.getElementById("caption").hidden=false;
  document.getElementById("caption-step").textContent=step;
  document.getElementById("caption-title").textContent=title;
  document.getElementById("caption-body").textContent=body;
};
window.clearCaption=()=>{document.getElementById("caption").hidden=true;document.body.classList.remove("recording")};
refresh();setInterval(refresh,1000);
