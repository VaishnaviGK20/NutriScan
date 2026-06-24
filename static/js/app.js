/* ===== NutriScan India — Frontend JS ===== */

// =================== TOAST ===================
const _toastWrap = () => document.getElementById('toastWrap');

function showToast(msg, type = 'info', duration = 3200) {
  const wrap = _toastWrap();
  if (!wrap) return;
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(() => {
    t.style.opacity = '0';
    t.style.transition = 'opacity .3s';
    setTimeout(() => t.remove(), 320);
  }, duration);
}

// =================== OTP BOXES ===================
function initOtpBoxes() {
  const boxes = document.querySelectorAll('.otp-box');
  if (!boxes.length) return;

  boxes.forEach((box, i) => {
    box.addEventListener('input', (e) => {
      const val = e.target.value.replace(/\D/g, '');
      e.target.value = val.slice(-1);
      if (val && i < boxes.length - 1) boxes[i + 1].focus();
      if (val) e.target.classList.add('filled');
      else e.target.classList.remove('filled');
      checkOtpComplete(boxes);
    });

    box.addEventListener('keydown', (e) => {
      if (e.key === 'Backspace' && !box.value && i > 0) {
        boxes[i - 1].value = '';
        boxes[i - 1].classList.remove('filled');
        boxes[i - 1].focus();
      }
      if (e.key === 'ArrowLeft' && i > 0) boxes[i - 1].focus();
      if (e.key === 'ArrowRight' && i < boxes.length - 1) boxes[i + 1].focus();
    });

    box.addEventListener('paste', (e) => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData)
        .getData('text').replace(/\D/g, '').slice(0, 6);
      text.split('').forEach((ch, idx) => {
        if (boxes[idx]) {
          boxes[idx].value = ch;
          boxes[idx].classList.add('filled');
        }
      });
      const nextEmpty = Array.from(boxes).findIndex(b => !b.value);
      if (nextEmpty !== -1) boxes[nextEmpty].focus();
      else boxes[boxes.length - 1].focus();
      checkOtpComplete(boxes);
    });
  });

  if (boxes[0]) boxes[0].focus();
}

function checkOtpComplete(boxes) {
  const code = Array.from(boxes).map(b => b.value).join('');
  if (code.length === 6) {
    const form = boxes[0].closest('form');
    if (form) {
      const hidden = form.querySelector('#otp-hidden');
      if (hidden) { hidden.value = code; form.submit(); }
    }
  }
}

// =================== IMAGE PREVIEW ===================
function initUploadZone() {
  const zone    = document.getElementById('uploadZone');
  const input   = document.getElementById('foodImage');
  const preview = document.getElementById('imgPreview');
  const camBtn  = document.getElementById('camBtn'); // legacy

  if (!zone) return;

  zone.addEventListener('click', () => input && input.click());

  // drag-and-drop
  zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('drag'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('drag'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault(); zone.classList.remove('drag');
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  });

  if (input) input.addEventListener('change', () => input.files[0] && setFile(input.files[0]));

  // camera capture input (triggered by inline onclick in template)
  const camInput = document.getElementById('camInput');
  if (camInput) camInput.addEventListener('change', () => camInput.files[0] && setFile(camInput.files[0]));

  // legacy camBtn support (old template)
  if (camBtn && camInput) {
    camBtn.addEventListener('click', (e) => { e.stopPropagation(); camInput.click(); });
  }

  function setFile(file) {
    if (!file.type.startsWith('image/')) {
      showToast('Please choose an image file.', 'err'); return;
    }
    const url = URL.createObjectURL(file);
    if (preview) { preview.src = url; preview.style.display = 'block'; }
    if (zone) zone.style.display = 'none';
    // keep a reference for the scan button
    window._scanFile = file;
    document.getElementById('scanBtn') && (document.getElementById('scanBtn').disabled = false);
  }
}

// =================== FOOD SCAN ===================
function initScanButton() {
  const btn = document.getElementById('scanBtn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    if (!window._scanFile) { showToast('Pick an image first.', 'err'); return; }

    const desc = (document.getElementById('description') || {}).value || '';
    const fd = new FormData();
    fd.append('image', window._scanFile);
    fd.append('description', desc);

    showSection('loading');

    try {
      const res = await fetch('/api/scan', { method: 'POST', body: fd });
      const data = await res.json();

      if (!res.ok || data.error) {
        showToast(data.error || 'Scan failed.', 'err');
        showSection('upload');
        return;
      }

      renderResults(data);
      showSection('results');
    } catch (err) {
      showToast('Network error. Check your connection.', 'err');
      showSection('upload');
    }
  });
}

function showSection(id) {
  ['upload', 'loading', 'results'].forEach(s => {
    const el = document.getElementById(`section-${s}`);
    if (el) el.style.display = (s === id) ? '' : 'none';
  });
}

function renderResults(data) {
  const n = data.total_nutrition || {};

  // detected image
  const img = document.getElementById('resultImg');
  if (img && data.image_b64) img.src = `data:image/jpeg;base64,${data.image_b64}`;

  // chips
  const chips = document.getElementById('detectedChips');
  if (chips) {
    chips.innerHTML = (data.detected_items || [])
      .map(it => `<span class="chip">${it}</span>`).join('');
    if (!data.detected_items?.length) {
      chips.innerHTML = '<span class="chip" style="background:rgba(229,57,53,.08);color:#E53935;border-color:rgba(229,57,53,.3)">No food detected — add manually below</span>';
    }
  }

  // nutrition cards
  setNut('nutCal',  n.calories, 'kcal');
  setNut('nutProt', n.protein,  'g');
  setNut('nutFat',  n.fat,      'g');
  setNut('nutCarb', n.carbs,    'g');
  setNut('nutFib',  n.fiber,    'g');

  // explanations
  const expl = document.getElementById('explContent');
  if (expl) {
    expl.innerHTML = (data.explanations || [])
      .map(e => `<p>${mdBold(e)}</p>`).join('');
  }

  // pre-fill "add to log" form
  window._lastScan = n;
}

function setNut(id, val, unit) {
  const el = document.getElementById(id);
  if (el) el.textContent = `${val ?? 0}${unit}`;
}

function mdBold(str) {
  return str.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>');
}

// =================== ADD SCAN TO LOG ===================
function initAddToLog() {
  const btn = document.getElementById('addToLogBtn');
  if (!btn) return;

  btn.addEventListener('click', async () => {
    const n = window._lastScan;
    if (!n) return;
    const meal = (document.getElementById('mealSelect') || {}).value || 'snack';
    const qty  = parseFloat((document.getElementById('qtyInput') || {}).value) || 1;

    try {
      const res = await fetch('/api/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          food_name: document.getElementById('foodNameForLog')?.value || 'Scanned food',
          quantity:  qty,
          calories:  (n.calories || 0) / qty,
          protein:   (n.protein  || 0) / qty,
          fat:       (n.fat      || 0) / qty,
          carbs:     (n.carbs    || 0) / qty,
          fiber:     (n.fiber    || 0) / qty,
          meal_type: meal,
        }),
      });
      const d = await res.json();
      if (d.success) showToast('Added to today\'s log! 🎉', 'ok');
      else showToast(d.error || 'Failed to add.', 'err');
    } catch {
      showToast('Network error.', 'err');
    }
  });
}

// =================== FOOD SEARCH ===================
function initFoodSearch() {
  const input = document.getElementById('foodSearch');
  const results = document.getElementById('searchResults');
  if (!input || !results) return;

  let timer = null;

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { results.style.display = 'none'; return; }
    timer = setTimeout(() => fetchSearch(q), 280);
  });

  document.addEventListener('click', (e) => {
    if (!input.contains(e.target) && !results.contains(e.target))
      results.style.display = 'none';
  });

  async function fetchSearch(q) {
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const items = await res.json();
      renderSearch(items);
    } catch {}
  }

  function renderSearch(items) {
    if (!items.length) { results.style.display = 'none'; return; }
    results.innerHTML = items.map(it => `
      <div class="search-item" data-key="${it.key}"
           data-cal="${it.calories}" data-prot="${it.protein}"
           data-fat="${it.fat}" data-carb="${it.carbs}" data-fib="${it.fiber}"
           data-name="${it.name}">
        <span>
          <div class="search-item-name">${it.name}</div>
          <div class="search-item-cal">${it.calories} kcal &bull; P:${it.protein}g F:${it.fat}g C:${it.carbs}g</div>
        </span>
        <button class="search-item-add">+ Add</button>
      </div>`).join('');
    results.style.display = 'block';

    results.querySelectorAll('.search-item').forEach(row => {
      row.querySelector('.search-item-add').addEventListener('click', async (e) => {
        e.stopPropagation();
        await addManualFood(row);
      });
    });
  }

  async function addManualFood(row) {
    const meal = (document.getElementById('mealSelectSearch') || {}).value || 'snack';
    const qty  = parseFloat((document.getElementById('qtySearch') || {}).value) || 1;

    try {
      const res = await fetch('/api/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          food_name: row.dataset.name,
          quantity:  qty,
          calories:  parseFloat(row.dataset.cal) || 0,
          protein:   parseFloat(row.dataset.prot) || 0,
          fat:       parseFloat(row.dataset.fat) || 0,
          carbs:     parseFloat(row.dataset.carb) || 0,
          fiber:     parseFloat(row.dataset.fib) || 0,
          meal_type: meal,
        }),
      });
      const d = await res.json();
      if (d.success) {
        showToast(`${row.dataset.name} added! 🎉`, 'ok');
        input.value = '';
        results.style.display = 'none';
        if (typeof refreshTracker === 'function') refreshTracker();
      } else {
        showToast(d.error || 'Failed.', 'err');
      }
    } catch {
      showToast('Network error.', 'err');
    }
  }
}

// =================== DELETE LOG ITEM ===================
function initDeleteLog() {
  document.querySelectorAll('.del-log-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.id;
      if (!id) return;
      try {
        const res = await fetch(`/api/log/${id}`, { method: 'DELETE' });
        const d = await res.json();
        if (d.success) {
          btn.closest('.log-item')?.remove();
          showToast('Item removed.', 'ok');
        }
      } catch {
        showToast('Could not delete.', 'err');
      }
    });
  });
}

// =================== CALORIE RING ===================
function initCalorieRing() {
  const svg = document.getElementById('calorieRing');
  if (!svg) return;
  const consumed = parseFloat(svg.dataset.consumed) || 0;
  const goal     = parseFloat(svg.dataset.goal) || 2000;
  const pct      = Math.min(consumed / goal, 1);
  const r = 64, circ = 2 * Math.PI * r;
  const circle = svg.querySelector('.ring-progress');
  if (circle) {
    circle.style.strokeDasharray = circ;
    circle.style.strokeDashoffset = circ * (1 - pct);
    circle.style.stroke = consumed > goal ? '#E53935' : '#FF6B35';
  }
}

// =================== MACRO BARS ===================
function initMacroBars() {
  document.querySelectorAll('.macro-fill').forEach(bar => {
    const pct = Math.min(parseFloat(bar.dataset.pct) || 0, 100);
    bar.style.width = pct + '%';
  });
}

// =================== HISTORY CHART ===================
function initHistoryChart() {
  const container = document.getElementById('historyBars');
  if (!container) return;
  const bars = container.querySelectorAll('.hbar-fill');
  const max = Math.max(...Array.from(bars).map(b => parseFloat(b.dataset.val) || 0), 1);
  bars.forEach(bar => {
    const val = parseFloat(bar.dataset.val) || 0;
    bar.style.height = Math.max((val / max) * 100, val > 0 ? 4 : 2) + '%';
  });
}

// =================== EXPLANATION TOGGLE ===================
function initExplToggle() {
  const btn = document.getElementById('explToggle');
  const block = document.getElementById('explContent');
  if (!btn || !block) return;
  block.style.display = 'none';
  btn.addEventListener('click', () => {
    const hidden = block.style.display === 'none';
    block.style.display = hidden ? 'block' : 'none';
    btn.querySelector('.expl-arrow').textContent = hidden ? '▲' : '▼';
  });
}

// =================== RETRY UPLOAD ===================
function initRetry() {
  const btn = document.getElementById('retryBtn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    window._scanFile = null;
    const preview = document.getElementById('imgPreview');
    const zone = document.getElementById('uploadZone');
    if (preview) { preview.src = ''; preview.style.display = 'none'; }
    if (zone) zone.style.display = '';
    showSection('upload');
  });
}

// =================== INIT ===================
document.addEventListener('DOMContentLoaded', () => {
  initOtpBoxes();
  initUploadZone();
  initScanButton();
  initAddToLog();
  initFoodSearch();
  initDeleteLog();
  initCalorieRing();
  initMacroBars();
  initHistoryChart();
  initExplToggle();
  initRetry();
});
