/* Predial360 Onboarding Tour */
(function () {
  'use strict';

  const CSS = `
    #p360-overlay {
      position: fixed; inset: 0; z-index: 9998;
      pointer-events: auto; background: transparent; cursor: default;
    }
    .p360-spotlight {
      position: relative !important; z-index: 9999 !important;
      box-shadow: 0 0 0 9999px rgba(6,11,20,0.85) !important;
      outline: 2px solid rgba(59,130,246,0.55) !important;
      outline-offset: 4px !important; border-radius: 6px;
      pointer-events: none !important;
    }
    #p360-tooltip {
      position: fixed; z-index: 10000; width: 320px;
      background: linear-gradient(135deg,rgba(15,23,42,0.97) 0%,rgba(30,41,59,0.97) 100%);
      border: 1px solid rgba(59,130,246,0.3); border-radius: 16px;
      padding: 22px 24px 18px; color: #e2e8f0;
      box-shadow: 0 30px 70px rgba(0,0,0,0.65), 0 0 0 1px rgba(59,130,246,0.08);
      backdrop-filter: blur(12px); font-family: inherit;
    }
    .p360-badge {
      display: inline-block;
      background: rgba(59,130,246,0.18); border: 1px solid rgba(59,130,246,0.38);
      color: #60a5fa; font-size: 11px; font-weight: 700;
      letter-spacing: 1px; text-transform: uppercase;
      padding: 2px 10px; border-radius: 20px; margin-bottom: 11px;
    }
    #p360-tooltip h5 {
      color: #f1f5f9; font-size: 15px; font-weight: 700;
      margin: 0 0 7px; line-height: 1.35;
    }
    #p360-tooltip p {
      color: #94a3b8; font-size: 13.5px; line-height: 1.65; margin: 0 0 15px;
    }
    .p360-dots { display: flex; gap: 5px; margin-bottom: 14px; align-items: center; }
    .p360-dot {
      height: 7px; border-radius: 4px; background: rgba(59,130,246,0.28);
      transition: all 0.25s; width: 7px;
    }
    .p360-dot.active { background: #3b82f6; width: 18px; }
    .p360-actions { display: flex; justify-content: space-between; align-items: center; }
    .p360-btn-next {
      background: linear-gradient(135deg,#3b82f6 0%,#2563eb 100%);
      color: #fff; border: none; padding: 8px 22px; border-radius: 8px;
      font-size: 13.5px; font-weight: 600; cursor: pointer;
      transition: opacity 0.18s, transform 0.12s;
    }
    .p360-btn-next:hover { opacity: 0.88; transform: translateY(-1px); }
    .p360-btn-skip {
      background: none; border: none; color: #475569; font-size: 12.5px;
      cursor: pointer; padding: 4px 6px; transition: color 0.18s;
    }
    .p360-btn-skip:hover { color: #94a3b8; }
    .p360-pulse {
      position: fixed; z-index: 10001; pointer-events: none;
      width: 18px; height: 18px; border-radius: 50%; background: #3b82f6;
      animation: p360_pulse 1.4s infinite;
      box-shadow: 0 0 0 0 rgba(59,130,246,0.7);
    }
    @keyframes p360_pulse {
      0%   { box-shadow: 0 0 0 0   rgba(59,130,246,0.7); }
      70%  { box-shadow: 0 0 0 12px rgba(59,130,246,0);   }
      100% { box-shadow: 0 0 0 0   rgba(59,130,246,0);   }
    }
  `;

  window.Predial360Tour = {
    _steps: [],
    _cur: 0,
    _doneKey: null,
    _el: null,    // spotlight element
    _ov: null,    // overlay
    _tt: null,    // tooltip
    _pulse: null, // pulse dot

    _css() {
      if (!document.getElementById('p360-css')) {
        const s = document.createElement('style');
        s.id = 'p360-css'; s.textContent = CSS;
        document.head.appendChild(s);
      }
    },

    _done() { return localStorage.getItem(this._doneKey) === '1'; },

    _finish() {
      localStorage.setItem(this._doneKey, '1');
      sessionStorage.removeItem('p360_cont');
      this._clean();
    },

    start(userId, steps) {
      this._doneKey = 'p360tour_' + userId;
      if (this._done()) return;
      this._steps = steps; this._cur = 0;
      this._css();
      this._show();
    },

    resume(userId, steps) {
      this._doneKey = 'p360tour_' + userId;
      if (this._done()) return;
      if (!sessionStorage.getItem('p360_cont')) return;
      this._steps = steps; this._cur = 0;
      this._css();
      this._show();
    },

    _show() {
      this._clean();
      const step = this._steps[this._cur];
      if (!step) { this._finish(); return; }

      const target = document.querySelector(step.sel);
      if (!target) { this._advance(); return; }

      target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });

      // Overlay
      this._ov = document.createElement('div');
      this._ov.id = 'p360-overlay';
      document.body.appendChild(this._ov);

      // Spotlight
      this._el = target;
      target.classList.add('p360-spotlight');

      // Pulse dot at element center
      const r = target.getBoundingClientRect();
      this._pulse = document.createElement('div');
      this._pulse.className = 'p360-pulse';
      this._pulse.style.left = (r.left + r.width / 2 - 9) + 'px';
      this._pulse.style.top  = (r.top  + r.height / 2 - 9) + 'px';
      document.body.appendChild(this._pulse);

      // Tooltip
      const total = this._steps.length;
      const isLast = this._cur === total - 1;
      const btnLabel = step.btnLabel || (isLast ? 'Concluir ✓' : 'Próximo →');

      this._tt = document.createElement('div');
      this._tt.id = 'p360-tooltip';
      this._tt.innerHTML =
        `<div class="p360-badge">Passo ${this._cur + 1} de ${total}</div>
         <h5>${step.title}</h5>
         <p>${step.text}</p>
         <div class="p360-dots">${this._steps.map((_, i) =>
           `<div class="p360-dot${i === this._cur ? ' active' : ''}"></div>`
         ).join('')}</div>
         <div class="p360-actions">
           <button class="p360-btn-skip">${isLast ? '' : 'Pular tour'}</button>
           <button class="p360-btn-next">${btnLabel}</button>
         </div>`;
      document.body.appendChild(this._tt);
      this._place(r);

      this._tt.querySelector('.p360-btn-next').onclick = () => {
        if (step.action) { step.action(); }
        else { this._advance(); }
      };
      this._tt.querySelector('.p360-btn-skip').onclick = () => this._finish();
    },

    _place(r) {
      const tt = this._tt;
      const W = window.innerWidth, H = window.innerHeight;
      const ttW = 320, ttH = tt.offsetHeight || 210;
      const pad = 14;

      const candidates = [
        { l: r.right + 20,          t: r.top + r.height / 2 - ttH / 2 },  // right
        { l: r.left - ttW - 20,     t: r.top + r.height / 2 - ttH / 2 },  // left
        { l: r.left + r.width/2 - ttW/2, t: r.bottom + 20 },              // below
        { l: r.left + r.width/2 - ttW/2, t: r.top - ttH - 20 }            // above
      ];

      let best = null, bestScore = -Infinity;
      for (const c of candidates) {
        const cl = Math.max(pad, Math.min(c.l, W - ttW - pad));
        const ct = Math.max(pad, Math.min(c.t, H - ttH - pad));
        const score = -(Math.abs(cl - c.l) + Math.abs(ct - c.t));
        if (score > bestScore) { bestScore = score; best = { l: cl, t: ct }; }
      }
      tt.style.left = best.l + 'px';
      tt.style.top  = best.t + 'px';
    },

    _advance() {
      this._cur++;
      if (this._cur >= this._steps.length) this._finish();
      else this._show();
    },

    _clean() {
      if (this._el)    { this._el.classList.remove('p360-spotlight'); this._el = null; }
      if (this._ov)    { this._ov.remove();    this._ov = null; }
      if (this._tt)    { this._tt.remove();    this._tt = null; }
      if (this._pulse) { this._pulse.remove(); this._pulse = null; }
    }
  };
})();
