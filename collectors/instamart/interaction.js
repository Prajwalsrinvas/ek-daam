// INSTAMART v1.3 (bad-draw dump + one reload; v1.2 robust probes after slow draws; v1.1 speed: all wait_network_idle removed — Swiggy never idles, each call hit BD's 30 s cap = 150 s/run) — B12-shaped, copied line-for-line from ../blinkit/interaction.js where the logic is
// the same. Rows are built HERE, in the interaction, from parse()'s RETURN value: tag values
// auto-inject into parse()'s return as <field> + <field>_url, and parser.<field> NEVER
// populates live. collect() is called ONCE with an array. Only endpoints we CONSUME are
// tagged — a tagged endpoint returning a bad status kills the run.
// Money on Instamart is {units,nanos} -> rupees = units + nanos/1e9. NEVER /100 (Zepto-only quirk).
// One row per VARIATION (multipacks are separate rows — honest, they are separate SKUs).
const keyword = input.keyword;
const pincode = input.pincode;

if (!keyword) { bad_input('missing keyword'); }
if (!pincode) { bad_input('missing pincode'); }

// ---8<--- ROWBUILD START (pure; depends only on Money. Unit-tested offline by
// a harness outside this repo, which slices this block out of THIS file. Keep the markers.)
const imGrid = (c) => { try { const cc = c && c.card && c.card.card; const gi = cc && cc.gridElements && cc.gridElements.infoWithStyle; return (gi && gi.items && gi.items.length) ? gi.items : null; } catch (eG) { return null; } };
const imItems = (root) => { const out = []; try { const cards = (root && root.cards) || []; for (let i = 0; i < cards.length; i++) { const it = imGrid(cards[i]); if (!it) { continue; } for (let k = 0; k < it.length; k++) { out.push(it[k]); } } } catch (eI) { } return out; };
const imNorm = (raw) => { let r = raw; if (typeof r === 'string') { try { r = JSON.parse(r); } catch (eN) { r = null; } } if (!r || typeof r !== 'object') { return null; } const d = r.data ? r.data : r; if (!d || !d.cards || !d.cards.length) { return null; } if (!imItems(d).length) { return null; } return d; };
// Per-pod delivery SLA, e.g. {"1382868":6,"1403746":12}. Item-level `sla` is null in the sample,
// so this config map is the real ETA source (6 mins == the header "6 mins Delivery to ...").
const imPodEta = (root) => { const map = {}; try { const arr = (root && root.configs && root.configs.IM_PAGE_CONFIGS && root.configs.IM_PAGE_CONFIGS.configInfo) || []; for (let i = 0; i < arr.length; i++) { const pd = (arr[i] && arr[i].card && arr[i].card.podDetailsList) || []; for (let k = 0; k < pd.length; k++) { const p = pd[k]; const sla = p && p.serviceabilityDetails && p.serviceabilityDetails.sla; const v = sla ? sla.value : null; const n = (v === undefined || v === null || String(v) === '') ? NaN : parseInt(String(v), 10); if (p && p.podId && !isNaN(n)) { map[String(p.podId)] = n; } } } } catch (eP) { } return map; };
const imPrimaryPod = (root) => { try { const arr = (root && root.configs && root.configs.IM_PAGE_CONFIGS && root.configs.IM_PAGE_CONFIGS.configInfo) || []; for (let i = 0; i < arr.length; i++) { const pd = (arr[i] && arr[i].card && arr[i].card.podDetailsList) || []; for (let k = 0; k < pd.length; k++) { if (pd[k] && pd[k].priority === 'PRIORITY_PRIMARY' && pd[k].podId) { return String(pd[k].podId); } } } } catch (eR) { } return null; };
// podId is the location proof (Blinkit's merchant_id / Zepto's storeId).
const imFirstStore = (root) => { try { const its = imItems(root); for (let i = 0; i < its.length; i++) { const vs = (its[i] && its[i].variations) || []; for (let k = 0; k < vs.length; k++) { if (vs[k] && vs[k].podId) { return String(vs[k].podId); } } } } catch (eF) { } return imPrimaryPod(root); };
// Keyword echo — Instamart has no "Showing results for" snippet; the per-item analytics carry it.
const imQueryEcho = (root) => { try { const its = imItems(root); for (let i = 0; i < its.length; i++) { const a = its[i] && its[i].analytics; const ef = a && a.extraFields; const s = (ef && ef.searchString) || (a && a.objectValue); if (s) { return String(s); } } } catch (eQ) { } return null; };
const imRupees = (m) => { if (m === undefined || m === null) { return null; } if (typeof m === 'number') { return isNaN(m) ? null : m; } if (typeof m === 'string') { const n0 = parseFloat(m.replace(/[^0-9.]/g, '')); return isNaN(n0) ? null : n0; } const u = (m.units === undefined || m.units === null) ? 0 : parseFloat(String(m.units)); const na = (m.nanos === undefined || m.nanos === null) ? 0 : Number(m.nanos); if (isNaN(u)) { return null; } const v = u + (isNaN(na) ? 0 : na / 1e9); return isNaN(v) ? null : v; };
const imMoney = (m) => { const n = imRupees(m); if (n === null) { return null; } try { return new Money(n, 'INR'); } catch (eM) { return n; } };
// Ad markers PROVEN in the sample: item.badges[] carries {type:'BADGE_TYPE_AD', text:'Ad'} and
// item.adTrackingContext is a non-empty ad string. Both agree on exactly the same 4 items (12
// variations). NOTE: 'BADGE' itself contains the substring 'AD' — match on 'TYPE_AD', never 'AD'.
const imSponsored = (it) => { try { const b = (it && it.badges) || []; for (let i = 0; i < b.length; i++) { const t = String((b[i] && b[i].type) || ''); const x = String((b[i] && b[i].text) || '').trim(); if (t.indexOf('TYPE_AD') > -1 || /^ad$/i.test(x)) { return true; } } } catch (eB) { } try { const a = it && it.adTrackingContext; if (typeof a === 'string' && a.length > 0) { return true; } } catch (eA) { } return false; };
const imOOS = (it, v) => { try { if (it && it.inStock === false) { return true; } if (it && it.isAvail === false) { return true; } if (v && v.inventory && v.inventory.inStock === false) { return true; } if (v && v.slotInfo && v.slotInfo.isAvail === false) { return true; } } catch (eO) { } return false; };
// No stock COUNT exists in the payload (inventory = {inStock, lowStockText}); cartAllowedQuantity
// is an order cap ("Only N unit(s) ... per order"), not stock -> null unless lowStockText names a
// number ("Only 2 left"). All 72 sample variations have lowStockText:"" -> all null. Honest.
const imQty = (v) => { try { const t = String((v && v.inventory && v.inventory.lowStockText) || ''); const m = t.match(/(\d+)/); if (m) { return parseInt(m[1], 10); } } catch (eQ2) { } return null; };
const imEta = (v, podMap, pageEta) => { try { const sla = v && v.sla; if (sla) { const raw = (sla.value !== undefined && sla.value !== null) ? sla.value : sla.deliveryTime; const n = parseInt(String(raw), 10); if (!isNaN(n)) { return n; } const tx = String(sla.text || sla.title || ''); const m = tx.match(/(\d+)\s*min/i); if (m) { return parseInt(m[1], 10); } } const pid = (v && v.podId) ? String(v.podId) : null; if (pid && podMap && podMap[pid] !== undefined) { return podMap[pid]; } } catch (eE) { } return (pageEta === undefined) ? null : pageEta; };
// image_url is TEXT, never new Image() (finding 9c: it THROWS in the live sandbox). The sample
// carries no absolute URLs — only ids — so the base is derived from the live DOM when possible
// (ctx.img_base) and falls back to Swiggy's documented CDN root. imageIds excludes videos.
const imImage = (v, base) => { try { const ids = (v && v.imageIds) || []; let id = null; for (let i = 0; i < ids.length; i++) { if (ids[i]) { id = String(ids[i]); break; } } if (id === null) { const md = (v && v.medias) || []; for (let k = 0; k < md.length; k++) { if (md[k] && md[k].id && String(md[k].type || '').indexOf('IMAGE') > -1) { id = String(md[k].id); break; } } } if (id === null) { return null; } if (id.indexOf('http') === 0) { return id; } const b = base ? String(base) : 'https://media-assets.swiggy.com/swiggy/image/upload/'; return b + id; } catch (eI2) { return null; } };
// 18-field contract, identical to Zepto B12 / Blinkit v1 (BD auto-appends the job's `input`
// object to every dataset row, so {keyword, pincode} rides along for free — no extra column
// needed. Those two ARE the whole input: no coordinates are sent, because the pincode is
// typed into the site).
const imBuildRows = (root, ctx) => { const rows = []; const seen = {}; ctx.row_errors = 0; const podMap = imPodEta(root); const its = imItems(root); for (let i = 0; i < its.length; i++) { const it = its[i]; const vs = (it && it.variations) || []; const spon = imSponsored(it); for (let k = 0; k < vs.length; k++) { try { const v = vs[k]; if (!v) { continue; } let id = v.skuId || v.spinId || null; if (!id) { id = String(v.displayName || it.displayName || '') + '|' + String(v.quantityDescription || ''); } id = String(id); if (!id || seen[id]) { continue; } seen[id] = 1; const pr = v.price || {}; const off = (pr.offerPrice !== undefined && pr.offerPrice !== null) ? pr.offerPrice : pr.mrp; const rv = (v.rating && v.rating.value !== undefined && v.rating.value !== null && String(v.rating.value) !== '') ? parseFloat(String(v.rating.value)) : null; rows.push({ product_name: v.displayName || it.displayName || null, brand: v.brandName || it.brand || null, package_size: v.quantityDescription || null, product_id: id, mrp: imMoney((pr.mrp !== undefined && pr.mrp !== null) ? pr.mrp : off), selling_price: imMoney(off), discounted_selling_price: imMoney(off), out_of_stock: imOOS(it, v), available_quantity: imQty(v), is_sponsored: spon, rating: (rv === null || isNaN(rv)) ? null : rv, image_url: imImage(v, ctx.img_base), serp_screenshot: ctx.shot || null, store_id: v.podId ? String(v.podId) : null, requested_pincode: ctx.pincode || null, resolved_area: ctx.resolved_area || null, eta_minutes: imEta(v, podMap, ctx.page_eta), captured_at: ctx.captured_at }); } catch (eRow) { ctx.row_errors = ctx.row_errors + 1; } } } return rows; };
// ---8<--- ROWBUILD END

// --- DOM helpers (interaction-only; NOT part of ROWBUILD). Instamart's controls are anchored by
// TEXT ("Add your location", "Confirm Location") and Studio selectors are CSS-only, so on a ladder
// miss we DERIVE a selector from the live html() around the text anchor and click that. Derivation
// is safe even for hashed classes because we derive and click within the same page state.
const imDump = (h, needle, back, fwd) => { try { const lo = String(h).toLowerCase(); const i = lo.indexOf(String(needle).toLowerCase()); if (i < 0) { return 'ANCHOR_NOT_FOUND(' + needle + ')'; } return String(h).slice(Math.max(0, i - back), i + fwd).replace(/\s+/g, ' '); } catch (eD) { return 'DUMP_ERR'; } };
const imTextAround = (h, needle, back, fwd) => { try { const lo = String(h).toLowerCase(); const i = lo.indexOf(String(needle).toLowerCase()); if (i < 0) { return null; } return String(h).slice(Math.max(0, i - back), i + fwd).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim(); } catch (eT) { return null; } };
const imAttrSel = (tag) => { let m = tag.match(/data-testid="([^"<>]{1,80})"/); if (m) { return '[data-testid="' + m[1] + '"]'; } m = tag.match(/data-test-id="([^"<>]{1,80})"/); if (m) { return '[data-test-id="' + m[1] + '"]'; } m = tag.match(/\sid="([A-Za-z_][A-Za-z0-9_-]{0,60})"/); if (m) { return '#' + m[1]; } m = tag.match(/aria-label="([^"<>]{1,60})"/); if (m) { return '[aria-label="' + m[1] + '"]'; } m = tag.match(/class="([^"<>]{1,300})"/); if (m) { const toks = m[1].split(/\s+/).filter((t) => /^[A-Za-z_][A-Za-z0-9_-]{2,60}$/.test(t)); if (toks.length) { let best = toks[0]; for (let i = 1; i < toks.length; i++) { if (toks[i].length > best.length) { best = toks[i]; } } return '.' + best; } } return null; };
const imSelAt = (h, i) => { let p = i; for (let k = 0; k < 8; k++) { p = h.lastIndexOf('<', p - 1); if (p < 0 || i - p > 1500) { return null; } const tag = h.slice(p, Math.min(h.length, p + 400)); if (tag.indexOf('</') === 0) { continue; } if (/^<(input|script|style|meta|link|title|textarea)\b/i.test(tag)) { return null; } const s = imAttrSel(tag); if (s) { return s; } } return null; };
const imSelNear = (h, needle) => { try { const lo = String(h).toLowerCase(); const nd = String(needle).toLowerCase(); let from = 0; for (let n = 0; n < 4; n++) { const i = lo.indexOf(nd, from); if (i < 0) { return null; } const s = imSelAt(String(h), i); if (s) { return s; } from = i + 1; } } catch (eS) { } return null; };
const imPick = (cands, t0, t1) => { for (let i = 0; i < cands.length; i++) { if (el_exists(cands[i], (i === 0) ? t0 : t1)) { return cands[i]; } } return null; };

// Page 1 = any search/v2 call WITHOUT a non-zero offset (B12/Blinkit-proven lookahead: last-match-
// wins meant a page-2 prefetch overwrote page 1 on Blinkit). Page 2 tagged separately + merged.
// Broad fallback third. UNVERIFIED: the page-2 URL shape — the sample says data.pageOffset =
// {nextOffset:"1"} and searchResultsOffset:32, so page 2 is most likely ?offset=1 (page index),
// which this lookahead excludes correctly; an item-style offset=32 is excluded too. A param named
// like `page_offset=1` would poison the page-1 tag -> search_api_any is the safety net.
tag_response('search_api', /api\/instamart\/search\/v2(?![^"']*offset=[1-9])/);
tag_response('search_p2', /api\/instamart\/search\/v2[^"']*offset=[1-9]/);
tag_response('search_api_any', /api\/instamart\/search\/v2/);

country('in');
console.log('INSTAMART-V1 ROUTING=country-in tag_response-only');
console.log('KEYWORD=' + keyword + ' PINCODE=' + pincode);
// Non-swiggy hosts are ignored so the Google-map tiles on the confirm screen cannot hold the idle
// open (finding #31: wait_network_idle.timeout is idleness-REQUIRED, not a cap -> hang risk).
const IGN = [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*swiggy\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro|awswaf|instrumentation/i];
const SEARCH_URL = 'https://www.swiggy.com/instamart/search?custom_back=true&query=' + encodeURIComponent(keyword);

// Location is REQUIRED on Instamart (no default store), so unlike Blinkit we start on the homepage
// and there is no pre-bind payload worth snapshotting.
navigate('https://www.swiggy.com/instamart');
let shellOk = el_exists('[data-testid]', 25000);
console.log('SHELL=' + shellOk + ' HEADER=' + el_exists('[data-testid="address-bar"], [data-testid="search-location"]', 15000));
// Bad draws (2026-08-23 morning): no data-testid for 25 s, header never visible. Dump what the page actually is, then
// reload once (Zepto B12's WAF-challenge lesson) before giving up on the draw.
if (!shellOk) { try { const hB = html() || ''; const tB = (hB.match(/<title[^>]*>([^<]*)</i) || ['', ''])[1]; const bodyB = hB.replace(/<script[\s\S]*?<\/script>/gi, ' ').replace(/<style[\s\S]*?<\/style>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 900); console.log('BAD_DRAW title=' + tB + ' len=' + hB.length + ' waf=' + /awswaf|challenge|captcha/i.test(hB) + ' body=' + bodyB); } catch (eBD) { console.log('BAD_DRAW_DUMP_ERR=' + eBD); } console.log('RELOAD_ONCE'); navigate('https://www.swiggy.com/instamart'); shellOk = el_exists('[data-testid]', 30000); console.log('SHELL2=' + shellOk); }
let h0 = html() || '';
console.log('LOAD1 len=' + h0.length + ' WAF_MARKERS=' + /awswaf|challenge\.js|captcha/i.test(h0));
console.log('HOME_HDR=' + imDump(h0, 'Add your location', 350, 900));

// --- Location choreography (Instamart steps 1-4). EVERY selector below is
// UNVERIFIED; each step dumps the DOM around its text anchor on a miss so ONE preview teaches the
// real selectors (the Blinkit lesson: the modal may even auto-open and the header click times out).
const INPUT_CANDS = ['input[placeholder*="area" i]', 'input[placeholder*="street" i]', 'input[placeholder*="locality" i]', 'input[placeholder*="address" i]'];
// Preview #6: a HIDDEN input[placeholder*="area"] is pre-rendered on the homepage -> el_exists says true, type() times out.
// Never trust the early probe; always open the modal via search-location first (diag v3/v4 proved that path).
let pinInput = null;
console.log('EARLY_INPUT_PROBE=skipped (hidden pre-rendered input)');

// Diag v2-v4 (2026-08-22): header = [data-testid="address-bar"] (absent on the no-store layout); the modal's
// "Search for an area or address" control = [data-testid="search-location"] (no <input> until it is clicked).
// v1.2: the header renders 10-60 s after the shell on slow draws, and search-location can exist while HIDDEN
// (modal closed). Loop: click address-bar when it appears, then wait for search-location to be VISIBLE.
let modalReady = false;
for (let k = 0; k < 6 && !modalReady; k++) { if (el_exists('[data-testid="address-bar"]', 8000)) { try { click('[data-testid="address-bar"]'); console.log('CLICKED=address-bar k=' + k); wait_page_idle(); } catch (eAB) { console.log('ADDRESS_BAR_CLICK_ERR=' + eAB); } } try { wait_visible('[data-testid="search-location"]', {timeout: 6000}); modalReady = true; } catch (eV) { console.log('SEARCH_LOCATION_NOT_VISIBLE k=' + k); } }
if (modalReady) { try { click('[data-testid="search-location"]'); console.log('CLICKED=search-location'); wait_page_idle(); pinInput = imPick(INPUT_CANDS, 10000, 1500); } catch (eSL) { console.log('SEARCH_LOCATION_CLICK_ERR=' + eSL); } }
if (pinInput === null) { let trig = imPick(['[data-testid="search-location"]', '[data-testid="address-bar"]', '[data-testid*="address" i]', '[data-testid*="location" i]', '[aria-label*="location" i]', '[class*="LocationBar" i]'], 8000, 2000); if (trig === null) { trig = imSelNear(h0, 'Add your location'); console.log('DERIVED trigger(add-your-location)=' + trig); } if (trig === null) { trig = imSelNear(h0, 'To see items in your area'); console.log('DERIVED trigger(to-see-items)=' + trig); } console.log('SEL location_trigger=' + trig); if (trig === null) { console.log('HOME_HDR2=' + imDump(h0, 'To see items', 350, 700)); blocked('instamart location trigger not found'); } click(trig); wait_page_idle(); }

// Dialog "Share location to find the closest Instamart store" -> "Search for an area or address".
if (pinInput === null) { const hD = html() || ''; console.log('DLG_DUMP=' + imDump(hD, 'closest Instamart', 400, 1100)); console.log('DLG_DUMP2=' + imDump(hD, 'Search for an area', 350, 700)); let sBtn = imPick(['[data-testid="search-location"]', '[data-testid*="search-area" i]', '[data-testid*="addressSearch" i]', '[data-testid*="search" i][role="button"]'], 4000, 1500); if (sBtn === null) { sBtn = imSelNear(hD, 'Search for an area'); console.log('DERIVED search_area=' + sBtn); } console.log('SEL search_area_button=' + sBtn); if (sBtn !== null) { click(sBtn); wait_page_idle(); } }

// The textbox. NEVER input[type="text"] and never a bare placeholder*="Search" — both matched
// the PRODUCT search box on Zepto and typed the pincode into it.
if (pinInput === null) { pinInput = imPick(INPUT_CANDS, 15000, 2000); }
if (pinInput === null) { const hI = html() || ''; console.log('INPUT_DUMP=' + imDump(hI, 'street name', 400, 800)); console.log('INPUT_DUMP2=' + imDump(hI, '<input', 250, 700)); }
console.log('SEL pincode_input=' + pinInput);
if (pinInput === null) { blocked('instamart location input not found'); }
type(pinInput, pincode, {replace: true});

// Suggestions took 20-30s on Zepto -> long probe + retype fallback + long probe again (finding 1).
let sugg = imPick(['div._11n32:nth-child(2)', '[class*="icon-location-marker"]', '[data-testid*="suggestion" i]', '[data-testid*="address-item" i]', '[class*="Suggestion" i]'], 15000, 2000);
if (sugg === null) { console.log('RETYPE_PINCODE'); type(pinInput, pincode, {replace: true}); sugg = imPick(['[data-testid*="suggestion" i]', '[class*="Suggestion" i]'], 15000, 2000); }
const hS = html() || '';
console.log('SUGG_DUMP=' + imDump(hS, pincode, 450, 1300));
// The site's own resolution of the pincode is provenance even if the header never echoes it.
let chosenArea = imTextAround(hS, pincode, 60, 220);
console.log('CHOSEN_SUGGESTION=' + (chosenArea ? chosenArea.slice(0, 140) : null));
if (sugg === null) { sugg = imSelNear(hS, pincode); console.log('DERIVED suggestion=' + sugg); }
console.log('SEL suggestion=' + sugg);
if (sugg === null) { blocked('no instamart location suggestions rendered'); }
click(sugg);
wait_page_idle();

// MAP CONFIRM screen (Google map + "SELECT DELIVERY LOCATION" + button "Confirm Location").
// UNVERIFIED whether the Studio worker even gets this screen -> treat it as OPTIONAL: probe, click
// if found, otherwise log and continue. The real proof of the bind is podId in the payload.
let confirmSel = imPick(['button.sc-iGgWBj', '[class*="sc-dcJsrY"] button', 'button[data-testid*="confirm" i]', '[data-testid*="confirm" i]'], 30000, 2500);
const hM = html() || '';
console.log('MAP_DUMP=' + imDump(hM, 'Confirm Location', 500, 700));
console.log('MAP_DUMP2=' + imDump(hM, 'SELECT DELIVERY LOCATION', 300, 800));
if (confirmSel === null) { confirmSel = imSelNear(hM, 'Confirm Location'); console.log('DERIVED confirm=' + confirmSel); }
console.log('SEL confirm_button=' + confirmSel);
if (confirmSel !== null) { click(confirmSel); wait_page_idle(); }
// v1.3: a fast draw (05:17Z) clicked Confirm but the bind did not take ("Device location is turned off" stayed on screen)
// and the search then 403'd - which KILLS a tagged run. Verify the header says "Delivery to <pincode>" before searching,
// re-clicking Confirm up to 3x; give up with blocked() rather than touching the tagged endpoint unbound.
let bound = false;
for (let b = 0; b < 3 && !bound; b++) { try { wait_visible('[data-testid="address-line"]', {timeout: 8000}); } catch (eAL) { } const hBind = html() || ''; bound = new RegExp('Delivery to[^<]{0,40}' + pincode, 'i').test(hBind) || (hBind.indexOf('data-testid="address-line"') > -1 && hBind.indexOf(pincode) > -1); console.log('BIND_CHECK b=' + b + ' bound=' + bound); if (!bound) { const cs2 = imPick(['button.sc-iGgWBj', '[class*="sc-dcJsrY"] button'], 6000, 2000); if (cs2 !== null) { try { click(cs2); console.log('RECONFIRM_CLICKED'); wait_page_idle(); } catch (eRC) { console.log('RECONFIRM_ERR=' + eRC); } } } }
if (!bound) { const hNB = html() || ''; console.log('UNBOUND_DUMP=' + hNB.replace(/<script[\s\S]*?<\/script>/gi, ' ').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 600)); blocked('instamart location bind did not take after 3 confirms'); }
if (confirmSel === null) { console.log('MAP_CONFIRM=absent -> continuing (UNVERIFIED: may not be shown to this worker)'); }
try { wait_hidden(pinInput, {timeout: 15000}); console.log('DIALOG_CLOSED=true'); } catch (eDlg) { console.log('DIALOG_CLOSED=timeout'); }

// Header after confirm: "6 mins Delivery to <pincode>, <area>, ..." = resolved_area +
// ETA. Read it HERE, not on the SERP — the search page may not carry the location header.
const hH = html() || '';
let resolvedArea = null;
let headerEta = null;
const hdrTxt = imTextAround(hH, 'Delivery to', 140, 200);
console.log('HEADER_TEXT=' + (hdrTxt ? hdrTxt.slice(0, 200) : null));
if (hdrTxt) { const mA = hdrTxt.match(/Delivery to[^|]{0,140}/i); if (mA) { resolvedArea = mA[0].replace(/\s+/g, ' ').trim().slice(0, 140); } const mE = hdrTxt.match(/(\d+)\s*mins?\b/i); if (mE) { headerEta = parseInt(mE[1], 10); } }
if (resolvedArea === null) { const around = imTextAround(hH, pincode, 60, 160); if (around) { resolvedArea = around.slice(0, 140); } }
if (resolvedArea === null) { resolvedArea = chosenArea ? chosenArea.slice(0, 140) : null; console.log('LOC_PROOF_WEAK: header did not echo the location — falling back to the chosen suggestion text; podId in the payload is the real proof'); }
console.log('RESOLVED_AREA_AFTER_CONFIRM=' + resolvedArea + ' HEADER_ETA=' + headerEta);

// --- SERP. Direct search-URL navigation is allowed on Instamart (choreography step 5, robots-clean).
const HYD = ['img[src*="/image/upload/"]', '[data-testid*="item" i]', '[data-testid*="product" i]', '[class*="Product" i]', 'div[data-testid]'];
navigate(SEARCH_URL);
const hyd1 = imPick(HYD, 25000, 2500);
console.log('HYDRATION_MARKER1=' + hyd1);
try { wait_for_parser_value('search_api', {timeout: 20000}); console.log('WFPV=ok'); } catch (eW) { console.log('WFPV=timeout'); }

let out = parse();
console.log('SA_URL=' + out.search_api_url);
let root = imNorm(out.search_api);
if (root === null) { root = imNorm(out.search_api_any); console.log('FELL_BACK_TO_ANY=' + (root !== null)); }
let store1 = imFirstStore(root);
console.log('POST_NAV ITEMS=' + imItems(root).length + ' STORE=' + store1 + ' ECHO=' + imQueryEcho(root));

// Empty payload OR no podId (= location never applied) -> one re-navigate, exactly like Blinkit's
// stale-store fallback. Costs 1 extra load when it fires. UNVERIFIED which case is normal here.
if (root === null || store1 === null) { console.log('REFRESH_NAVIGATE root=' + (root !== null) + ' store=' + store1); navigate(SEARCH_URL); const hyd2 = imPick(HYD, 20000, 2500); console.log('HYDRATION_MARKER2=' + hyd2); try { wait_for_parser_value('search_api', {timeout: 20000}); console.log('WFPV2=ok'); } catch (eW2) { console.log('WFPV2=timeout'); } out = parse(); root = imNorm(out.search_api); if (root === null) { root = imNorm(out.search_api_any); } store1 = imFirstStore(root); console.log('POST_REFRESH ITEMS=' + imItems(root).length + ' STORE=' + store1); }
if (root === null) { const hF = html() || ''; console.log('FAIL_DUMP=' + imDump(hF, 'instamart', 200, 600) + ' WAF_MARKERS=' + /awswaf|captcha|challenge/i.test(hF)); blocked('no instamart search payload captured'); }

// Derive the image CDN prefix from the live DOM by locating a known imageId inside an <img src>.
// Falls back to Swiggy's documented root inside imImage(). UNVERIFIED until one preview logs it.
let imgBase = null;
try { const its0 = imItems(root); let sid = null; for (let i = 0; i < its0.length && sid === null; i++) { const vs0 = its0[i].variations || []; for (let k = 0; k < vs0.length && sid === null; k++) { const ids0 = vs0[k].imageIds || []; if (ids0.length && String(ids0[0]).length > 6) { sid = String(ids0[0]); } } } if (sid !== null) { const hI2 = html() || ''; const p2 = hI2.indexOf(sid); if (p2 > -1) { let s2 = p2; while (s2 > 0 && '"\'( \n\t'.indexOf(hI2.charAt(s2 - 1)) === -1) { s2 = s2 - 1; } const pref = hI2.slice(s2, p2); if (/^https?:\/\//i.test(pref)) { imgBase = pref; } } console.log('IMG_ID_SAMPLE=' + sid.slice(0, 70) + ' IMG_BASE_DERIVED=' + imgBase); } } catch (eIB) { console.log('IMG_BASE_ERR=' + eIB); }

// Screenshot BEFORE the row build so every row carries it (B12's fix).
tag_screenshot('serp_screenshot', {full_page: false});
const outS = parse();
const shotVal = outS.serp_screenshot_url || outS.serp_screenshot || null;
console.log('SHOT_TYPE=' + (typeof shotVal) + ' SHOT=' + String(shotVal).slice(0, 90));
console.log('SERP_DOM resolved_area=' + outS.resolved_area + ' page_eta=' + outS.page_eta + ' n_img_cdn=' + outS.n_img_cdn);

const ctx = { pincode: pincode, resolved_area: resolvedArea || outS.resolved_area || null, page_eta: (headerEta !== null) ? headerEta : ((outS.page_eta === undefined) ? null : outS.page_eta), shot: shotVal, img_base: imgBase, captured_at: new Date().toISOString(), row_errors: 0 };
let rows = imBuildRows(root, ctx);
try { const root2 = imNorm(out.search_p2); if (root2 !== null) { const rows2 = imBuildRows(root2, ctx); const ids = {}; for (let i = 0; i < rows.length; i++) { ids[rows[i].product_id] = 1; } let added = 0; for (let j = 0; j < rows2.length; j++) { if (!ids[rows2[j].product_id]) { rows.push(rows2[j]); added = added + 1; } } console.log('PAGE2_MERGED=' + added + ' P2_URL=' + String(out.search_p2_url).slice(0, 130)); } else { console.log('PAGE2_MERGED=none'); } } catch (eP2) { console.log('PAGE2_ERR=' + eP2); }

const distinct = {};
for (let i = 0; i < rows.length; i++) { if (rows[i].store_id) { distinct[rows[i].store_id] = 1; } }
let nSpon = 0;
let nOos = 0;
for (let i = 0; i < rows.length; i++) { if (rows[i].is_sponsored) { nSpon = nSpon + 1; } if (rows[i].out_of_stock) { nOos = nOos + 1; } }

console.log('SOURCE=interaction_tag');
console.log('QUERY_ECHO=' + imQueryEcho(root));
console.log('RESOLVED_AREA=' + ctx.resolved_area);
console.log('STORE_ID=' + (rows.length ? rows[0].store_id : null) + ' STORE_DISTINCT=' + Object.keys(distinct).length + ' PRIMARY_POD=' + imPrimaryPod(root));
console.log('ROW_COUNT=' + rows.length + ' ROW_ERRORS=' + ctx.row_errors + ' SPONSORED=' + nSpon + ' OOS=' + nOos);
if (!rows.length) { blocked('no rows from payload'); }
collect(rows);
