// ZEPTO parser. In the LIVE path the app consumes only `resolved_area` and
// `eta_minutes` from this file, through parse()'s return value — the rows
// themselves are built in interaction.js from the tagged search payload.
// The product walk below (rowOf / walk / the embedded-blob fallback) is the
// OFFLINE-HARNESS path: it is what parses a saved payload outside a run. It
// is kept, not deleted, because that harness is how a payload gets checked
// without spending a collector credit.
let dbg_probe = 'ok';
try { new Money(1, 'INR'); } catch (eP1) { dbg_probe = 'MONEY_FAIL'; }
try { new Image('https://x/y.png'); } catch (eP2) { dbg_probe = dbg_probe + '|IMAGE_FAIL'; }
let sa_sniff = 'undef';
try { const sa0 = parser.search_api; if (sa0 !== undefined && sa0 !== null) { sa_sniff = (typeof sa0) + ':' + (typeof sa0 === 'string' ? sa0.slice(0, 40) : Object.keys(sa0 || {}).slice(0, 6).join(',')); } if (sa0 === null) { sa_sniff = 'null'; } } catch (eS0) { sa_sniff = 'SNIFF_ERR'; }
let resp = parser.search_api;
if (typeof resp === 'string') { try { resp = JSON.parse(resp); } catch (e) { resp = null; } }
const sAll = parser.search_all;
if ((!resp || !resp.layout || !resp.layout.length) && sAll && sAll.length) { for (let k = sAll.length - 1; k >= 0; k--) { let c = sAll[k]; if (typeof c === 'string') { try { c = JSON.parse(c); } catch (e2) { c = null; } } if (c && c.layout && c.layout.length) { resp = c; break; } } }
if (!resp || !resp.layout || !resp.layout.length) { let c2 = parser.search_api_any; if (typeof c2 === 'string') { try { c2 = JSON.parse(c2); } catch (e3) { c2 = null; } } if (c2 && c2.layout && c2.layout.length) { resp = c2; } }
const locAll = parser.loc_all;
const shot = parser.serp_screenshot;
const CDN = 'https://cdn.zeptonow.com/production/';
const money = (v) => { if (v === undefined || v === null) { return null; } try { return new Money(v / 100, 'INR'); } catch (eM) { return v / 100; } };
const img = (u) => { if (!u) { return null; } try { return new Image(u); } catch (eI) { return u; } };
const captured_at = new Date().toISOString();
let eta_minutes = null;
let resolved_area = null;
try { const t = $('body').text() || ''; const m = t.match(/(\d+)\s*minutes/); if (m) { eta_minutes = parseInt(m[1], 10); } } catch (e) { eta_minutes = null; }
try { const pc = input.pincode; if (pc) { const el = $('button:contains("' + pc + '")').first(); if (el && el.length) { resolved_area = el.text_sane(); } } } catch (e) { resolved_area = null; }
let row_errors = 0;
const seen = {};
const products = [];
const rowOf = (p, sp) => { try { const pv = p.productVariant || {}; const id = pv.productId || p.id; if (!id || seen[id]) { return; } seen[id] = 1; const rs = pv.ratingSummary || {}; const ip = (pv.images && pv.images[0] && pv.images[0].path) ? CDN + pv.images[0].path : null; products.push({ product_name: (p.product && p.product.name) || null, brand: (p.product && p.product.brand) || null, package_size: pv.formattedPacksize || null, product_id: id, mrp: money(p.mrp), selling_price: money(p.sellingPrice), discounted_selling_price: money(p.discountedSellingPrice), out_of_stock: !!p.outOfStock, available_quantity: p.availableQuantity === undefined ? null : p.availableQuantity, is_sponsored: !!sp, rating: rs.averageRating === undefined ? null : rs.averageRating, image_url: img(ip), serp_screenshot: shot || null, store_id: p.storeId || null, requested_pincode: input.pincode || null, resolved_area: resolved_area, eta_minutes: eta_minutes, captured_at: captured_at }); } catch (eRow) { row_errors = row_errors + 1; } };
const walk = (n, sp) => { if (!n || typeof n !== 'object') { return; } if (Array.isArray(n)) { n.forEach((x) => walk(x, sp)); return; } if (n.productResponse && n.productResponse.product && n.productResponse.sellingPrice !== undefined) { rowOf(n.productResponse, sp); } Object.keys(n).forEach((k) => { if (k !== 'productResponse') { walk(n[k], sp); } }); };
let source = 'none';
const layout = (resp && resp.layout) || [];
if (layout.length) { source = 'tag_response'; layout.forEach((w) => { if (w && (w.widgetId === 'PRODUCT_GRID' || w.widgetId === 'PREMIUM_CONTEXTUAL_ADS_V2')) { walk(w, w.widgetId === 'PREMIUM_CONTEXTUAL_ADS_V2'); } }); }
const grab = (s, key) => { const out = []; let i = 0; while ((i = s.indexOf(key, i)) !== -1) { const j = s.indexOf('{', i + key.length); if (j === -1) { break; } let d = 0; let q = false; let esc = false; let k = j; for (; k < s.length; k++) { const c = s.charAt(k); if (esc) { esc = false; continue; } if (c === '\\') { esc = true; continue; } if (q) { if (c === '"') { q = false; } continue; } if (c === '"') { q = true; continue; } if (c === '{') { d++; } if (c === '}') { d--; if (d === 0) { break; } } } if (d === 0 && k < s.length) { out.push([j, s.slice(j, k + 1)]); i = k + 1; } else { i = j + 1; } } return out; };
const decodeLits = (s) => { const out = []; let i = 0; while ((i = s.indexOf('"', i)) !== -1) { let k = i + 1; let esc = false; for (; k < s.length; k++) { const c = s.charAt(k); if (esc) { esc = false; continue; } if (c === '\\') { esc = true; continue; } if (c === '"') { break; } } if (k >= s.length) { break; } const lit = s.slice(i, k + 1); if (lit.length > 200 && lit.indexOf('productResponse') !== -1) { try { out.push(JSON.parse(lit)); } catch (e) { } } i = k + 1; } return out; };
if (!products.length) { const texts = []; try { $('script').each(function () { const t = $(this).html() || ''; if (t && t.indexOf('sellingPrice') !== -1) { texts.push(t); } }); } catch (e) { } try { const whole = $.html() || ''; if (!texts.length && whole.indexOf('sellingPrice') !== -1) { texts.push(whole); } } catch (e) { } const variants = []; texts.forEach((t) => { variants.push(t); decodeLits(t).forEach((d) => variants.push(d)); }); variants.forEach((v) => { grab(v, '"productResponse":').forEach((pair) => { let p = null; try { p = JSON.parse(pair[1]); } catch (e) { p = null; } if (!p || !p.product || p.sellingPrice === undefined || p.sellingPrice === null) { return; } const ctx = v.slice(Math.max(0, pair[0] - 3000), pair[0]); const sp = ctx.lastIndexOf('PREMIUM_CONTEXTUAL_ADS_V2') > ctx.lastIndexOf('PRODUCT_GRID'); rowOf(p, sp); }); }); if (products.length) { source = 'embedded_blob'; } }
const store_id_seen = products.length ? products[0].store_id : null;
return { products: products, loc_count: (locAll && locAll.length) ? locAll.length : 0, resolved_area: resolved_area, store_id_seen: store_id_seen, source: source, search_all_len: (sAll && sAll.length) ? sAll.length : 0, dbg_probe: dbg_probe, sa_sniff: sa_sniff, row_errors: row_errors, eta_minutes: eta_minutes };
