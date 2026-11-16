// Muestra todas las fechas como DD/MM/AAAA, sin importar el idioma del navegador.
// El <input type="date"> original se conserva (oculto) y sigue enviando AAAA-MM-DD al servidor;
// encima se pone una casilla de texto DD/MM/AAAA y un botón que abre el calendario.
(function () {
  const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  const aTexto = iso => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || ''); return m ? `${m[3]}/${m[2]}/${m[1]}` : ''; };
  const aIso = txt => {
    const m = /^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2}|\d{4})$/.exec((txt || '').trim());
    if (!m) return null;
    let [, d, mo, a] = m; if (a.length === 2) a = '20' + a;
    const f = new Date(+a, +mo - 1, +d);
    if (f.getFullYear() !== +a || f.getMonth() !== +mo - 1 || f.getDate() !== +d) return null;
    return `${a}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  };

  function convertir(el) {
    if (el.dataset.ddmm) return;
    el.dataset.ddmm = '1';
    const caja = document.createElement('span');
    caja.className = 'fecha-dmy';
    const txt = document.createElement('input');
    txt.type = 'text'; txt.inputMode = 'numeric'; txt.autocomplete = 'off';
    txt.className = el.className; txt.placeholder = 'dd/mm/aaaa'; txt.maxLength = 10;
    if (el.required) { txt.required = true; el.required = false; }
    if (el.disabled) txt.disabled = true;
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'fecha-cal'; btn.title = 'Abrir calendario'; btn.tabIndex = -1;
    btn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';
    el.parentNode.insertBefore(caja, el);
    caja.append(txt, btn, el);
    el.classList.add('fecha-oculta'); el.tabIndex = -1;

    // si un script cambia el valor (p. ej. "editar"), la casilla visible también cambia
    Object.defineProperty(el, 'value', {
      configurable: true,
      get() { return desc.get.call(this); },
      set(v) { desc.set.call(this, v); txt.value = aTexto(desc.get.call(this)); txt.setCustomValidity(''); }
    });
    txt.value = aTexto(desc.get.call(el));

    const avisar = () => { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); };
    // pone las barras solas mientras se escribe: 16112026 -> 16/11/2026
    txt.addEventListener('input', e => {
      if (e.inputType && e.inputType.startsWith('delete')) return;
      // si escribieron "1/5/2026" con barras propias se respeta; si solo hay números se arma dd/mm/aaaa
      const v = txt.value;
      if (/^\d{1,2}\/\d{1,2}\//.test(v) && !/^\d{2}\/\d{2}\//.test(v)) return;
      let d = v.replace(/\D/g, '').slice(0, 8);
      if (d.length > 4) d = d.slice(0, 2) + '/' + d.slice(2, 4) + '/' + d.slice(4); else if (d.length > 2) d = d.slice(0, 2) + '/' + d.slice(2);
      txt.value = d;
    });
    txt.addEventListener('change', () => {
      const v = txt.value.trim();
      if (!v) { desc.set.call(el, ''); txt.setCustomValidity(''); avisar(); return; }
      const iso = aIso(v);
      if (!iso) { txt.setCustomValidity('Escribe la fecha como dd/mm/aaaa'); txt.reportValidity(); return; }
      txt.setCustomValidity(''); desc.set.call(el, iso); txt.value = aTexto(iso); avisar();
    });
    txt.addEventListener('keydown', e => { if (e.key === 'Enter') txt.dispatchEvent(new Event('change')); });
    el.addEventListener('change', () => { txt.value = aTexto(desc.get.call(el)); txt.setCustomValidity(''); });
    btn.addEventListener('click', () => {
      if (txt.disabled) return;
      try { el.showPicker(); } catch (e) { el.style.pointerEvents = 'auto'; el.focus(); el.click(); }
    });
    // antes de enviar, toma lo escrito aunque no hayan salido de la casilla
    el.form && el.form.addEventListener('submit', e => {
      const v = txt.value.trim();
      if (v && !aIso(v)) { e.preventDefault(); e.stopImmediatePropagation(); txt.setCustomValidity('Escribe la fecha como dd/mm/aaaa'); txt.reportValidity(); return; }
      desc.set.call(el, v ? aIso(v) : '');
    }, true);
  }

  const todo = raiz => raiz.querySelectorAll && raiz.querySelectorAll('input[type=date]').forEach(convertir);
  const iniciar = () => {
    todo(document);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
      if (n.nodeType !== 1) return;
      if (n.matches && n.matches('input[type=date]')) convertir(n); else todo(n);
    }))).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
})();

// Valores en pesos con separador de miles mientras se escribe: 397600 -> 397.600
// Aplica a las casillas name="valor", name="base" y a las que tengan data-pesos.
// El servidor ya entiende "397.600" como 397600.
(function () {
  const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  const SEL = 'input[name=valor]:not([type=hidden]), input[name=base]:not([type=hidden]), input[data-pesos]';
  const formatear = t => {
    let s = String(t ?? '').replace(/[$\s]/g, '');
    if (!s) return '';
    // "397600.0" o "1500,5" que vienen de un script: se toma la parte entera
    if (/^\d+[.,]\d{1,2}$/.test(s)) s = s.split(/[.,]/)[0];
    const d = s.replace(/\D/g, '').replace(/^0+(?=\d)/, '');
    return d ? Number(d).toLocaleString('es-CO') : '';
  };
  function convertir(el) {
    if (el.dataset.pesosOk) return;
    el.dataset.pesosOk = '1';
    if (!el.getAttribute('inputmode')) el.setAttribute('inputmode', 'numeric');
    Object.defineProperty(el, 'value', {
      configurable: true,
      get() { return desc.get.call(this); },
      set(v) { desc.set.call(this, formatear(v)); }
    });
    desc.set.call(el, formatear(desc.get.call(el)));
    // en captura: se formatea antes de que otros scripts lean el valor en su propio 'input'
    el.addEventListener('input', () => {
      const antes = desc.get.call(el), pos = el.selectionStart ?? antes.length;
      const digitosAntes = antes.slice(0, pos).replace(/\D/g, '').length;
      const nuevo = formatear(antes);
      if (nuevo === antes) return;
      desc.set.call(el, nuevo);
      // deja el cursor después del mismo dígito
      let i = 0, vistos = 0;
      while (i < nuevo.length && vistos < digitosAntes) { if (/\d/.test(nuevo[i])) vistos++; i++; }
      try { el.setSelectionRange(i, i); } catch (e) {}
    }, true);
  }
  const todo = r => r.querySelectorAll && r.querySelectorAll(SEL).forEach(convertir);
  const iniciar = () => {
    todo(document);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
      if (n.nodeType !== 1) return;
      if (n.matches && n.matches(SEL)) convertir(n); else todo(n);
    }))).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar); else iniciar();
})();
