// INSTAMART v1 parser — THIN, B12/blinkit-parser style. It deliberately does NOT read
// `parser.<tag>` for data: tag values never populate there live. Its only
// job is to hand the interaction cheap DOM facts; parse()'s RETURN carries these keys PLUS the
// auto-injected tag fields (search_api, search_p2, search_api_any, serp_screenshot + _url).
// Never name a returned key after a tag field — the injection overwrites same-named keys.
let dbg_probe = 'ok';
try { new Money(1, 'INR'); } catch (eP1) { dbg_probe = 'MONEY_FAIL'; }
try { new Image('https://x/y.png'); } catch (eP2) { dbg_probe = dbg_probe + '|IMAGE_FAIL'; }
// Diagnostic only — kept to keep proving that on Instamart. Nothing below depends on it.
let sa_sniff = 'undef';
try { const sa0 = parser.search_api; if (sa0 === null) { sa_sniff = 'null'; } else if (sa0 !== undefined) { sa_sniff = (typeof sa0) + ':' + (typeof sa0 === 'string' ? sa0.slice(0, 40) : Object.keys(sa0 || {}).slice(0, 6).join(',')); } } catch (eS0) { sa_sniff = 'SNIFF_ERR'; }

let body_text = '';
try { body_text = $('body').text() || ''; } catch (eB) { body_text = ''; }
// resolved_area = the Instamart header, "<n> mins Delivery to <pincode>, <street>, ...".
// The SERP may not carry it — the interaction reads it post-confirm too.
let resolved_area = null;
try { const m = body_text.match(/Delivery to[\s\S]{0,110}/i); if (m) { resolved_area = m[0].replace(/\s+/g, ' ').trim().slice(0, 140); } } catch (eR) { resolved_area = null; }
if (resolved_area === null) { try { const pc = input.pincode; if (pc) { const m2 = body_text.match(new RegExp('[\\s\\S]{0,50}' + pc + '[\\s\\S]{0,80}')); if (m2) { resolved_area = m2[0].replace(/\s+/g, ' ').trim().slice(0, 140); } } } catch (eR2) { resolved_area = null; } }
// page-level ETA — prefer the minutes printed right before "Delivery to"; per-row podId SLAs from
// the API payload override this in the row build.
let page_eta = null;
try { const w = body_text.match(/(\d+)\s*mins?\b[\s\S]{0,25}Delivery to/i); if (w) { page_eta = parseInt(w[1], 10); } } catch (eE) { page_eta = null; }
if (page_eta === null) { try { const e2 = body_text.match(/(\d+)\s*mins?\b/i); if (e2) { page_eta = parseInt(e2[1], 10); } } catch (eE2) { page_eta = null; } }

// Hydration / control marker counts + text-anchor presence. These are what the diag run reads to
// confirm or replace every UNVERIFIED selector in the interaction. No `i` attribute flags here —
// cheerio's css-select is stricter than the browser.
const cnt = (sel) => { try { const n = $(sel); return (n && n.length) ? n.length : 0; } catch (eC) { return 0; } };
const has = (t) => { try { return body_text.toLowerCase().indexOf(String(t).toLowerCase()) > -1; } catch (eH) { return false; } };
const n_img_cdn = cnt('img[src*="/image/upload/"]');
const n_testid = cnt('[data-testid]');
const n_testid_item = cnt('[data-testid*="item"]');
const n_testid_product = cnt('[data-testid*="product"]');
const n_class_product = cnt('[class*="Product"]');
const n_input = cnt('input');
const n_area_input = cnt('input[placeholder*="area"]');
const n_button = cnt('button');
const t_add_location = has('Add your location');
const t_closest_store = has('closest Instamart');
const t_search_area = has('Search for an area');
const t_confirm = has('Confirm Location');
const t_select_delivery = has('SELECT DELIVERY LOCATION');
const t_delivery_to = has('Delivery to');
let html_len = 0;
let n_podid = 0;
let n_skuid = 0;
let n_variations = 0;
try { const whole = $.html() || ''; html_len = whole.length; n_podid = whole.split('podId').length - 1; n_skuid = whole.split('skuId').length - 1; n_variations = whole.split('quantityDescription').length - 1; } catch (eW) { }

return { resolved_area: resolved_area, page_eta: page_eta, n_img_cdn: n_img_cdn, n_testid: n_testid, n_testid_item: n_testid_item, n_testid_product: n_testid_product, n_class_product: n_class_product, n_input: n_input, n_area_input: n_area_input, n_button: n_button, t_add_location: t_add_location, t_closest_store: t_closest_store, t_search_area: t_search_area, t_confirm: t_confirm, t_select_delivery: t_select_delivery, t_delivery_to: t_delivery_to, html_len: html_len, n_podid: n_podid, n_skuid: n_skuid, n_variations: n_variations, dbg_probe: dbg_probe, sa_sniff: sa_sniff };
