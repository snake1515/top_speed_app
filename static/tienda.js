// Carrito de la tienda: se guarda en el navegador y se valida con el servidor antes de pedir.
(() => {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const COP = v => '$ ' + Math.round(v || 0).toLocaleString('es-CO');
  const CLAVE = 'ts_carrito';
  let carrito = [];
  try { carrito = JSON.parse(localStorage.getItem(CLAVE) || '[]'); } catch (e) { carrito = []; }
  const guardar = () => { try { localStorage.setItem(CLAVE, JSON.stringify(carrito)); } catch (e) {} };

  function toast(t) {
    const d = document.createElement('div'); d.className = 's-toast'; d.textContent = t; document.body.appendChild(d);
    setTimeout(() => d.remove(), 1800);
  }
  function contar() { $('cnt').textContent = carrito.reduce((a, it) => a + it.cantidad, 0); }
  function pintar() {
    contar();
    if (!carrito.length) { $('c-lista').innerHTML = '<p class="empty">Tu carrito está vacío.</p>'; $('c-total').textContent = COP(0); $('ir-pedido').disabled = true; return; }
    $('ir-pedido').disabled = false;
    $('c-lista').innerHTML = carrito.map((it, i) => `<div class="s-ci">
      <img src="${esc(it.img || URLS.sinfoto)}" alt="">
      <div><b>${esc(it.nombre)}</b><br><small class="muted">${esc(it.codigo)} · ${COP(it.precio)} c/u</small>
        ${it.disp !== undefined && it.cantidad > it.disp ? `<br><small class="neg">Solo hay ${it.disp} disponible(s)</small>` : ''}
        <div class="ctl"><button type="button" data-i="${i}" data-d="-1">−</button><b style="min-width:24px;text-align:center">${it.cantidad}</b><button type="button" data-i="${i}" data-d="1">+</button>
          <button type="button" class="btn ghost sm" data-x="${i}">Quitar</button></div></div>
      <b>${COP(it.precio * it.cantidad)}</b></div>`).join('');
    $('c-total').textContent = COP(carrito.reduce((a, it) => a + it.precio * it.cantidad, 0));
  }
  async function refrescar() {
    if (!carrito.length) return pintar();
    try {
      const r = await fetch(URLS.disp, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }, body: JSON.stringify({ ids: [...new Set(carrito.map(i => i.id))] }) });
      const d = await r.json();
      carrito = carrito.filter(it => d[it.id] && d[it.id].precio > 0).map(it => ({ ...it, precio: d[it.id].precio, disp: d[it.id].disp, img: d[it.id].img || it.img }));
      guardar();
    } catch (e) {}
    pintar();
  }
  // cada variante (modelo de moto) es una línea aparte, pero el stock es del mismo repuesto
  const llave = it => it.id + '-' + (it.v || 0);
  const delMismo = id => carrito.filter(i => i.id === id).reduce((a, i) => a + i.cantidad, 0);
  window.agregarCarrito = function (p, cantidad = 1) {
    const k = llave(p);
    const ex = carrito.find(it => llave(it) === k);
    if (ex) ex.cantidad += cantidad; else carrito.push({ id: p.id, v: p.v || 0, nombre: p.nombre, codigo: p.codigo, precio: p.precio, img: p.img, cantidad, disp: p.disp });
    const it = carrito.find(it => llave(it) === k);
    if (p.disp !== undefined && delMismo(p.id) > p.disp) { it.cantidad = Math.max(it.cantidad - (delMismo(p.id) - p.disp), 0); toast(`Solo hay ${p.disp} disponible(s)`); }
    else toast('Agregado al carrito ✓');
    carrito = carrito.filter(i => i.cantidad > 0);
    guardar(); pintar();
  };
  function abrir() { $('drawer').classList.add('on'); $('drawer-bg').classList.add('on'); refrescar(); }
  function cerrar() { $('drawer').classList.remove('on'); $('drawer-bg').classList.remove('on'); }
  $('abrir-carrito').addEventListener('click', abrir);
  $('cerrar-carrito').addEventListener('click', cerrar);
  $('drawer-bg').addEventListener('click', cerrar);
  $('c-lista').addEventListener('click', e => {
    const b = e.target.closest('[data-d]');
    if (b) { const it = carrito[b.dataset.i]; it.cantidad = Math.max(1, it.cantidad + Number(b.dataset.d)); if (it.disp !== undefined) it.cantidad = Math.min(it.cantidad, Math.max(it.disp, 1)); guardar(); pintar(); }
    const x = e.target.closest('[data-x]');
    if (x) { carrito.splice(Number(x.dataset.x), 1); guardar(); pintar(); }
  });
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-add]'); if (!b) return;
    const p = JSON.parse(b.dataset.add);
    const q = b.dataset.qty ? Math.max(parseInt(document.getElementById(b.dataset.qty).value) || 1, 1) : 1;
    agregarCarrito(p, q);
  });
  // checkout
  $('ir-pedido').addEventListener('click', () => { cerrar(); $('m-pedido').classList.add('on'); $('f-pedido').nombre.focus(); });
  document.querySelectorAll('[data-cerrar]').forEach(b => b.addEventListener('click', () => b.closest('.modal-bg').classList.remove('on')));
  $('f-pedido').addEventListener('change', e => { if (e.target.name === 'entrega') $('f-dir').style.display = e.target.value === 'recoger' ? 'none' : ''; });
  $('f-pedido').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, btn = $('enviar');
    btn.disabled = true; btn.textContent = 'Enviando…';
    const datos = Object.fromEntries(new FormData(f).entries());
    datos.items = carrito.map(it => ({ producto_id: it.id, cantidad: it.cantidad, v: it.v || 0 }));
    let j;
    try {
      const r = await fetch(URLS.pedido, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }, body: JSON.stringify(datos) });
      j = await r.json();
    } catch (err) { j = { ok: false, errores: ['No hubo conexión. Intenta de nuevo.'] }; }
    btn.disabled = false; btn.textContent = 'Enviar pedido';
    if (!j.ok) { $('p-err').innerHTML = j.errores.map(m => `<div class="flash err">${esc(m)}</div>`).join(''); refrescar(); return; }
    carrito = []; guardar();
    location.href = j.url;
  });
  pintar();
  if (carrito.length) refrescar();
})();
