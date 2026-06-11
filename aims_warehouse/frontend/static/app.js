/**
 * A.I.M.S. Tool Warehouse — app.js
 * Handles:
 *   - Hero count ticker (requestAnimationFrame, 0 → target)
 *   - Ctrl+K command palette (Alpine-driven, htmx-fetched results)
 *   - Tool modal open/close (htmx swap into #modal-target)
 *   - Zone panel open/close (htmx swap into #zone-panel)
 *   - Client-side "stack" (localStorage)
 *   - Keyboard navigation in palette results
 */

/* ─── Count ticker ──────────────────────────────────────────────────────────── */
function initTicker() {
  const el = document.getElementById('hero-count');
  if (!el) return;
  const target = parseInt(el.dataset.target, 10);
  if (isNaN(target)) return;

  // If lottie is rendering the hero we leave the number to lottie; if the
  // element is present with data-target we own it.
  const DURATION = 2200; // ms
  const start = performance.now();

  // Ease-out cubic
  function ease(t) { return 1 - Math.pow(1 - t, 3); }

  function tick(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / DURATION, 1);
    const value = Math.round(ease(progress) * target);
    el.textContent = value.toLocaleString();
    if (progress < 1) requestAnimationFrame(tick);
  }

  el.textContent = '0';
  requestAnimationFrame(tick);
}

/* ─── Fill bars (animate width from 0 after page load) ──────────────────────── */
function initFillBars() {
  // The CSS transition handles the animation; we just need to trigger a reflow
  // by setting the actual CSS custom property after a tick.
  const bars = document.querySelectorAll('.zone-fill-bar[data-fill]');
  requestAnimationFrame(() => {
    bars.forEach(bar => {
      bar.style.setProperty('--fill-pct', bar.dataset.fill + '%');
    });
  });
}

/* ─── Zone panel toggle ──────────────────────────────────────────────────────── */
function openZone(category) {
  const target = document.getElementById('zone-panel');
  if (!target) return;

  const url = `/app/zone/${encodeURIComponent(category)}?page=0`;
  htmx.ajax('GET', url, {
    target: '#zone-panel',
    swap: 'innerHTML'
  });
  target.style.display = 'block';
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function closeZone() {
  const target = document.getElementById('zone-panel');
  if (target) {
    target.innerHTML = '';
    target.style.display = 'none';
  }
}

/* ─── Tool modal ─────────────────────────────────────────────────────────────── */
function openTool(name) {
  const url = `/app/tool/${encodeURIComponent(name)}`;
  htmx.ajax('GET', url, {
    target: '#modal-target',
    swap: 'innerHTML'
  });
}

function closeModal() {
  const target = document.getElementById('modal-target');
  if (target) target.innerHTML = '';
}

/* ─── Stack (client-side localStorage) ──────────────────────────────────────── */
function getStack() {
  try {
    return JSON.parse(localStorage.getItem('aims_stack') || '[]');
  } catch { return []; }
}

function saveStack(stack) {
  localStorage.setItem('aims_stack', JSON.stringify(stack));
}

function toggleStack(name, category) {
  const stack = getStack();
  const idx = stack.findIndex(t => t.name === name);
  if (idx >= 0) {
    stack.splice(idx, 1);
    return false; // removed
  } else {
    stack.push({ name, category, added: Date.now() });
    saveStack(stack);
    return true; // added
  }
  saveStack(stack);
}

function inStack(name) {
  return getStack().some(t => t.name === name);
}

/* ─── Alpine.js data components ─────────────────────────────────────────────── */
document.addEventListener('alpine:init', () => {

  // Command palette
  Alpine.data('palette', () => ({
    open: false,
    query: '',
    selectedIdx: -1,
    debounceTimer: null,

    init() {
      // Ctrl+K / Cmd+K
      window.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
          e.preventDefault();
          this.toggle();
        }
        if (e.key === 'Escape' && this.open) {
          this.close();
        }
      });
    },

    toggle() {
      if (this.open) { this.close(); } else { this.show(); }
    },

    show() {
      this.open = true;
      this.$nextTick(() => {
        const inp = this.$el.querySelector('.palette-input');
        if (inp) inp.focus();
      });
    },

    close() {
      this.open = false;
      this.query = '';
      this.selectedIdx = -1;
    },

    onInput() {
      this.selectedIdx = -1;
      clearTimeout(this.debounceTimer);
      this.debounceTimer = setTimeout(() => {
        const target = this.$el.querySelector('#palette-results-target');
        if (target) {
          htmx.ajax('GET', `/app/search?q=${encodeURIComponent(this.query)}`, {
            target: '#palette-results-target',
            swap: 'innerHTML'
          });
        }
      }, 140);
    },

    onKeydown(e) {
      const items = this.$el.querySelectorAll('.palette-result-item');
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.selectedIdx = Math.min(this.selectedIdx + 1, items.length - 1);
        this._focusItem(items);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.selectedIdx = Math.max(this.selectedIdx - 1, 0);
        this._focusItem(items);
      } else if (e.key === 'Enter') {
        const item = items[this.selectedIdx];
        if (item) {
          item.click();
          this.close();
        }
      }
    },

    _focusItem(items) {
      items.forEach((el, i) => {
        el.classList.toggle('selected', i === this.selectedIdx);
      });
      if (items[this.selectedIdx]) {
        items[this.selectedIdx].scrollIntoView({ block: 'nearest' });
      }
    },

    openResult(name) {
      this.close();
      openTool(name);
    }
  }));

});

/* ─── Add to Stack button helper (called from modal template) ─────────────────── */
function handleStackBtn(btn, name, category) {
  const added = toggleStack(name, category);
  if (added) {
    btn.textContent = '✓ in stack';
    btn.classList.add('in-stack');
  } else {
    btn.textContent = '+ add to stack';
    btn.classList.remove('in-stack');
  }
}

/* ─── Init on DOM ready ──────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  initTicker();
  initFillBars();

  // Close modal on overlay click
  document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) closeModal();
  });
});

// Re-init fill bars after htmx swaps (zone panel loads)
document.addEventListener('htmx:afterSwap', () => {
  initFillBars();
});
