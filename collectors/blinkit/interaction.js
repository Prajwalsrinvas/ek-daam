// BLINKIT v1 — B12-shaped (see ../zepto/interaction.js). Rows are built HERE, in the
// interaction, from parse()'s RETURN value: tag values are auto-injected into parse()'s
// return as <field> + <field>_url, and parser.<field> NEVER populates live.
// collect() is called ONCE with an array. Only endpoints we CONSUME are tagged —
// a tagged endpoint returning a bad status kills the run.
// Prices on Blinkit are RUPEES already — never /100 (that is a Zepto-only quirk).
const keyword = input.keyword;
const pincode = input.pincode;

if (!keyword) { bad_input('missing keyword'); }
if (!pincode) { bad_input('missing pincode'); }

// ---8<--- ROWBUILD START (pure; depends only on Money. Unit-tested offline by
// a harness outside this repo, which slices this block out of THIS file. Keep the markers.)
const bkNorm = (raw) => { let r = raw; if (typeof r === 'string') { try { r = JSON.parse(r); } catch (eN) { r = null; } } if (!r || typeof r !== 'object') { return null; } if (r.is_success === false) { return null; } const root = r.response ? r.response : (r.snippets ? r : null); if (!root || !root.snippets || !root.snippets.length) { return null; } return root; };
const bkHeader = (root) => { try { const sn = (root && root.snippets) || []; for (let i = 0; i < sn.length; i++) { const s = sn[i]; if (s && s.widget_type === 'image_text_vr_type_header' && s.data && s.data.title && s.data.title.text) { return s.data.title.text; } } } catch (eH) { } return null; };
const bkCart = (d) => { return (d && d.atc_action && d.atc_action.add_to_cart && d.atc_action.add_to_cart.cart_item) ? d.atc_action.add_to_cart.cart_item : null; };
const bkMerchant = (d) => { const ci = bkCart(d); const m = (d && d.merchant_id !== undefined && d.merchant_id !== null) ? d.merchant_id : ((d && d.meta && d.meta.merchant_id !== undefined && d.meta.merchant_id !== null) ? d.meta.merchant_id : ((ci && ci.merchant_id !== undefined && ci.merchant_id !== null) ? ci.merchant_id : null)); return (m === null || m === undefined) ? null : String(m); };
const bkFirstStore = (root) => { try { const sn = (root && root.snippets) || []; for (let i = 0; i < sn.length; i++) { const s = sn[i]; if (!s || !s.data || !/product_card_snippet/.test(s.widget_type || '')) { continue; } const m = bkMerchant(s.data); if (m) { return m; } } } catch (eF) { } return null; };
const bkRupees = (t) => { if (t === undefined || t === null) { return null; } const n = (typeof t === 'number') ? t : parseFloat(String(t).replace(/[^0-9.]/g, '')); return isNaN(n) ? null : n; };
const bkMoney = (v) => { const n = bkRupees(v); if (n === null) { return null; } try { return new Money(n, 'INR'); } catch (eM) { return n; } };
const bkEta = (d, pageEta) => { try { const pb = (d && d.product_badges) || []; for (let bi = 0; bi < pb.length; bi++) { const b = pb[bi]; if (b && b.type === 'ETA') { const tx = (b.text_data && b.text_data.text) || ''; const mT = tx.match(/(\d+)\s*min/i); if (mT) { return parseInt(mT[1], 10); } const iu = (b.image_data && b.image_data.url) || ''; const mI = iu.match(/(\d+)-min/i); if (mI) { return parseInt(mI[1], 10); } } } const eu = (d && d.eta_tag && d.eta_tag.image && d.eta_tag.image.url) || ''; const mU = eu.match(/(\d+)-min/i); if (mU) { return parseInt(mU[1], 10); } } catch (eT) { } return pageEta; };
const bkSponsored = (s) => { try { const ob = (s.data && s.data.overlay_badges) || []; for (let oi = 0; oi < ob.length; oi++) { const u = (ob[oi] && ob[oi].image && ob[oi].image.url) || ''; if (u.indexOf('assets/ui/ad') > -1) { return true; } } } catch (eO) { } try { if (JSON.stringify(s.tracking || {}).indexOf('ads_campaign_id') > -1) { return true; } } catch (eS) { } return false; };
// 18-field contract, identical to Zepto B12 (BD auto-appends the job's `input` object to every
// dataset row, so {keyword, pincode} rides along for free — no extra column needed. Those two
// ARE the whole input: no coordinates are sent, because the pincode is typed into the site).
const bkBuildRows = (root, ctx) => { const rows = []; const seen = {}; const sn = (root && root.snippets) || []; ctx.row_errors = 0; for (let i = 0; i < sn.length; i++) { const s = sn[i]; if (!s || !s.data || !/product_card_snippet/.test(s.widget_type || '')) { continue; } try { const d = s.data; const ci = bkCart(d); let id = (d.identity && d.identity.id) || (d.meta && d.meta.product_id) || (ci && ci.product_id !== undefined && ci.product_id !== null ? String(ci.product_id) : null); const nm = (d.name && d.name.text) || (ci && ci.product_name) || (d.display_name && d.display_name.text) || null; if (!id) { id = nm; } if (!id || seen[id]) { continue; } seen[id] = 1; const inv = (d.inventory !== undefined && d.inventory !== null) ? d.inventory : (ci ? ci.inventory : null); const sell = ci ? ci.price : bkRupees(d.normal_price && d.normal_price.text); let mrpV = ci ? ci.mrp : bkRupees(d.mrp && d.mrp.text); if (mrpV === undefined || mrpV === null) { mrpV = sell; } const rt = (d.rating && d.rating.bar && d.rating.bar.value !== undefined) ? d.rating.bar.value : null; rows.push({ product_name: nm, brand: (d.brand_name && d.brand_name.text) || (ci && ci.brand) || null, package_size: (d.variant && d.variant.text) || (ci && ci.unit) || null, product_id: String(id), mrp: bkMoney(mrpV), selling_price: bkMoney(sell), discounted_selling_price: bkMoney(sell), out_of_stock: (d.product_state === 'out_of_stock') || d.is_sold_out === true || inv === 0, available_quantity: (inv === undefined) ? null : inv, is_sponsored: bkSponsored(s), rating: rt, image_url: (d.image && d.image.url) || (ci && ci.image_url) || null, serp_screenshot: ctx.shot || null, store_id: bkMerchant(d), requested_pincode: ctx.pincode || null, resolved_area: ctx.resolved_area || null, eta_minutes: bkEta(d, ctx.page_eta), captured_at: ctx.captured_at }); } catch (eRow) { ctx.row_errors = ctx.row_errors + 1; } } return rows; };
// ---8<--- ROWBUILD END

// Both patterns hit the SAME endpoint (POST blinkit.com/v1/layout/search?...). Narrow first
// (must carry a q= param, which the keyword SERP call always does, per the pagination next_url
// in a captured response), broad second as the fallback — mirrors B12's search_api /
// search_api_any pair. No sibling Blinkit endpoint is known to need excluding; captures show
// pagination reuses
// this same path, so a lookahead exclusion would have nothing to exclude. UNVERIFIED.
// Diag #1: last-match-wins CONFIRMED — the page-2 prefetch (offset=12) overwrote page 1.
// So: page 1 = any layout/search call WITHOUT a non-zero offset (lookahead, B12-proven), page 2 tagged separately and merged.
tag_response('search_api', /v1\/layout\/search\?(?![^"']*offset=[1-9])/);
tag_response('search_p2', /v1\/layout\/search\?[^"']*offset=[1-9]/);
tag_response('search_api_any', /v1\/layout\/search/);

country('in');
console.log('BLINKIT-V1 ROUTING=country-in tag_response-only');
console.log('KEYWORD=' + keyword + ' PINCODE=' + pincode);
// Direct SERP navigation. The Browser-API zone 403s /s/* on robots, but Studio's
// auto-provisioned zone is full-access and the
// robots test returned 200 + a real SERP. If this ever comes back `ub_bad_endpoint_robots`,
// fall back to homepage -> SPA search choreography.
navigate('https://blinkit.com/s/?q=' + encodeURIComponent(keyword));

let hyd1 = null;
if (el_exists('div[role="button"][id]', 25000)) { hyd1 = 'div[role="button"][id]'; }
if (hyd1 === null && el_exists('[class*="Product"]', 3000)) { hyd1 = '[class*="Product"]'; }
console.log('HYDRATION_MARKER1=' + hyd1);
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*blinkit\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro/i]});

// Pre-bind snapshot: cheap (v1 parser is thin, parse() costs no page load) and it is the ONLY
// way to tell a stale pre-bind payload from a fresh post-bind one, since tag last-match-wins
// was not yet confirmed on Blinkit when this was drafted.
let preStore = null;
let preSnips = 0;
try { const p0 = parse(); let r0 = bkNorm(p0.search_api); if (r0 === null) { r0 = bkNorm(p0.search_api_any); } preSnips = r0 ? r0.snippets.length : 0; preStore = bkFirstStore(r0); console.log('PRE_BIND XHR_ON_DIRECT_LOAD=' + (r0 !== null) + ' SNIPPETS=' + preSnips + ' STORE=' + preStore); } catch (eP0) { console.log('PRE_BIND_ERR=' + eP0); }

// --- Location bind choreography (Blinkit steps 1-3: location bar -> type
// pincode -> click suggestion. NO map confirm on Blinkit.) Every selector below is UNVERIFIED
// (drafted from playwright recon + styled-components naming); the diag run replaces them.
try { const hL0 = html() || ''; const iL = hL0.search(/class="[^"]*Location/); console.log('LOC_OUTER=' + (iL > -1 ? hL0.slice(Math.max(0, iL - 150), iL + 900).replace(/\s+/g, ' ') : 'NO_CLASS_LOCATION')); const iH = hL0.indexOf('<header'); console.log('HEADER_OUTER=' + (iH > -1 ? hL0.slice(iH, iH + 900).replace(/\s+/g, ' ') : 'NO_HEADER')); } catch (eL0) { console.log('LOC_OUTER_ERR=' + eL0); }
// Preview #2: on a fresh desktop load Blinkit AUTO-OPENS its location modal
// (LocationDropDown__LocationModalContainer > LocationSelectorDesktopV1__*, textbox 'search delivery location').
// So: if the modal input is already there, skip the header click entirely (clicking the wrapper timed out).
const modalOpen = el_exists('input[placeholder*="delivery location" i]', 8000);
console.log('MODAL_AUTO_OPEN=' + modalOpen);
let locBtn = null;
let onHome = false;
if (!modalOpen && el_exists('[class*="LocationBar"]', 12000)) { locBtn = '[class*="LocationBar"]'; }
if (!modalOpen && locBtn === null && el_exists('[data-test-id*="location" i]', 2500)) { locBtn = '[data-test-id*="location" i]'; }
if (!modalOpen && locBtn === null && el_exists('[data-testid*="location" i]', 2500)) { locBtn = '[data-testid*="location" i]'; }
if (!modalOpen && locBtn === null && el_exists('[class*="LocationDropDown"] [class*="Location"]:not([class*="Modal"]):not([class*="Overlay"])', 2500)) { locBtn = '[class*="LocationDropDown"] [class*="Location"]:not([class*="Modal"]):not([class*="Overlay"])'; }
// UNVERIFIED #5: the SERP may hide the location header. Homepage carries it for sure.
if (!modalOpen && locBtn === null) { console.log('LOC_BTN_MISS_ON_SERP -> homepage fallback (costs 1 extra load)'); onHome = true; navigate('https://blinkit.com/'); }
if (onHome && el_exists('[class*="LocationBar"]', 12000)) { locBtn = '[class*="LocationBar"]'; }
if (locBtn === null && onHome && el_exists('[class*="Location"]', 2500)) { locBtn = '[class*="Location"]'; }
if (!modalOpen && locBtn === null) { const hB = html() || ''; let aB = hB.indexOf('Select Location'); if (aB === -1) { aB = hB.indexOf('Delivery in'); } if (aB === -1) { aB = hB.toLowerCase().indexOf('location'); } console.log('LOC_DOM=' + (aB > -1 ? hB.slice(Math.max(0, aB - 200), aB + 1000).replace(/\s+/g, ' ') : 'ANCHOR_NOT_FOUND')); }
console.log('SEL location_button=' + locBtn);
if (!modalOpen && locBtn === null) { blocked('location button not found'); }
if (!modalOpen) { click(locBtn); wait_page_idle(); }

// Dialog input. NEVER `input[type="text"]` and never a bare placeholder*="Search" — both
// matched the PRODUCT search box on Zepto and typed the pincode into it.
let pinInput = null;
if (el_exists('input[placeholder*="delivery location" i]', 15000)) { pinInput = 'input[placeholder*="delivery location" i]'; }
if (pinInput === null && el_exists('input[name="select-locality"]', 2500)) { pinInput = 'input[name="select-locality"]'; }
if (pinInput === null && el_exists('input[placeholder*="location" i]', 2500)) { pinInput = 'input[placeholder*="location" i]'; }
if (pinInput === null && el_exists('input[placeholder*="area" i]', 2500)) { pinInput = 'input[placeholder*="area" i]'; }
console.log('SEL pincode_input=' + pinInput);
if (pinInput === null) { const hI = html() || ''; const aI = hI.toLowerCase().indexOf('delivery location'); console.log('DIALOG_DOM=' + (aI > -1 ? hI.slice(Math.max(0, aI - 200), aI + 1000).replace(/\s+/g, ' ') : 'ANCHOR_NOT_FOUND')); }
if (pinInput === null) { blocked('location dialog input not found'); }
type(pinInput, pincode, {replace: true});
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*blinkit\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro/i]});

const hSug = html() || '';
let anchorIdx = hSug.indexOf('LocationSearchList');
if (anchorIdx === -1) { anchorIdx = hSug.indexOf(pincode); }
console.log('SUGG_DOM_A=' + (anchorIdx > -1 ? hSug.slice(Math.max(0, anchorIdx - 100), anchorIdx + 1200).replace(/\s+/g, ' ') : 'ANCHOR_NOT_FOUND'));

// Suggestions took 20-30s on Zepto -> long probe + retype fallback + long probe again
// (learned on Zepto). Blinkit's list is Places-backed, same expectation. UNVERIFIED selectors.
let sugg = null;
if (el_exists('[class*="LocationSearchList"]', 15000)) { sugg = '[class*="LocationSearchList"]'; }
if (sugg === null) { console.log('RETYPE_PINCODE'); type(pinInput, pincode, {replace: true}); wait_network_idle({timeout: 1500, ignore: [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*blinkit\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro/i]}); }
if (sugg === null && el_exists('[class*="LocationSearchList"]', 15000)) { sugg = '[class*="LocationSearchList"]'; }
if (sugg === null && el_exists('[class*="LocationSearch"] li', 3000)) { sugg = '[class*="LocationSearch"] li'; }
if (sugg === null && el_exists('[class*="Suggestion" i]', 3000)) { sugg = '[class*="Suggestion" i]'; }
if (sugg === null && el_exists('[class*="LocationSelector"] li', 3000)) { sugg = '[class*="LocationSelector"] li'; }
if (sugg === null && el_exists('[class*="address" i]', 3000)) { sugg = '[class*="address" i]'; }
console.log('SEL suggestion=' + sugg);
// Preview #3: Blinkit's SERP never echoes the bound location (header = logo + search + cart), so the
// suggestion we click IS the site's own resolution of the pincode -> keep it as resolved_area provenance.
let chosenArea = null;
try { const hS2 = html() || ''; const mL = hS2.match(/LocationLabel[^>]*>([^<]*)<[\s\S]{0,400}?LocationDetails[^>]*>([^<]*)</); if (mL) { chosenArea = (mL[1] + ', ' + mL[2]).replace(/\s+/g, ' ').trim().slice(0, 120); } } catch (eCA) { chosenArea = null; }
console.log('CHOSEN_SUGGESTION=' + chosenArea);
if (sugg === null) { const hF = html() || ''; const aF = hF.indexOf('LocationSearchList'); console.log('SUGG_DOM_FAIL=' + (aF > -1 ? hF.slice(aF, aF + 600).replace(/\s+/g, ' ') : 'CONTAINER_ABSENT')); }
if (sugg === null) { blocked('no location suggestions rendered'); }
click(sugg);
wait_page_idle();
try { wait_hidden(pinInput, {timeout: 20000}); console.log('DIALOG_CLOSED=true'); } catch (eD) { console.log('DIALOG_CLOSED=timeout'); }
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*blinkit\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro/i]});
if (onHome) { console.log('BACK_TO_SERP'); navigate('https://blinkit.com/s/?q=' + encodeURIComponent(keyword)); }

const hLoc = html() || '';
const areaMatch = hLoc.match(new RegExp('[^<>]{0,80}' + pincode + '[^<>]{0,80}'));
console.log('AREA_TEXT_AFTER_CHOREO=' + (areaMatch ? areaMatch[0].trim().slice(0, 120) : 'NOT_FOUND'));

let hyd2 = null;
if (el_exists('div[role="button"][id]', 15000)) { hyd2 = 'div[role="button"][id]'; }
if (hyd2 === null && el_exists('[class*="Product"]', 3000)) { hyd2 = '[class*="Product"]'; }
console.log('HYDRATION_MARKER2=' + hyd2);
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*blinkit\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro/i]});
try { wait_for_parser_value('search_api', {timeout: 15000}); console.log('WFPV=ok'); } catch (eW) { console.log('WFPV=timeout'); }

let out = parse();
console.log('POST_BIND SA_URL=' + out.search_api_url);
let root = bkNorm(out.search_api);
if (root === null) { root = bkNorm(out.search_api_any); }
let store1 = bkFirstStore(root);
console.log('POST_BIND SNIPPETS=' + (root ? root.snippets.length : 0) + ' STORE=' + store1);

// Re-navigate when the payload is missing OR still looks pre-bind (same merchant as before the
// bind). UNVERIFIED #4: whether Blinkit refetches the SERP on bind at all. Costs 1 extra load
// when it fires; if the pre/post merchant legitimately matches this burns a load for nothing —
// watch STORE= in the log and drop the `stale` half of the condition if that happens.
const stale = (root !== null) && (preStore !== null) && (store1 === preStore);
if (root === null || stale) { console.log('REFRESH_NAVIGATE stale=' + stale); navigate('https://blinkit.com/s/?q=' + encodeURIComponent(keyword)); const hyd3 = el_exists('div[role="button"][id]', 20000) || el_exists('[class*="Product"]', 3000); console.log('HYDRATION_MARKER3=' + hyd3); wait_network_idle({timeout: 1500, ignore: [/^blob:/, /^(?!https?:\/\/([a-z0-9-]+\.)*blinkit\.com\/)/i, /analytics|telemetry|metrics|\/events?\b|\/track|sentry|faro/i]}); try { wait_for_parser_value('search_api', {timeout: 15000}); console.log('WFPV3=ok'); } catch (eW3) { console.log('WFPV3=timeout'); } out = parse(); root = bkNorm(out.search_api); if (root === null) { root = bkNorm(out.search_api_any); } store1 = bkFirstStore(root); console.log('POST_REFRESH SNIPPETS=' + (root ? root.snippets.length : 0) + ' STORE=' + store1); }
if (root === null) { blocked('no search payload captured after bind'); }

// Screenshot BEFORE the row build so every row carries it (B12's fix). Our flow never opens
// the in-page search overlay, so the location header should be in frame (choreography note 7).
tag_screenshot('serp_screenshot', {full_page: false});
const outS = parse();
const shotVal = outS.serp_screenshot_url || outS.serp_screenshot || null;
console.log('SHOT_TYPE=' + (typeof shotVal) + ' SHOT=' + String(shotVal).slice(0, 90));

const ctx = { pincode: pincode, resolved_area: outS.resolved_area || chosenArea || null, page_eta: (outS.page_eta === undefined ? null : outS.page_eta), shot: shotVal, captured_at: new Date().toISOString(), row_errors: 0 };
let rows = bkBuildRows(root, ctx);
try { const root2 = bkNorm(out.search_p2); if (root2 !== null) { const rows2 = bkBuildRows(root2, ctx); const ids = {}; for (let i = 0; i < rows.length; i++) { ids[rows[i].product_id] = 1; } let added = 0; for (let j = 0; j < rows2.length; j++) { if (!ids[rows2[j].product_id]) { rows.push(rows2[j]); added++; } } console.log('PAGE2_MERGED=' + added + ' P2_URL=' + String(out.search_p2_url).slice(0, 120)); } else { console.log('PAGE2_MERGED=none'); } } catch (eP2) { console.log('PAGE2_ERR=' + eP2); }
const distinct = {};
for (let i = 0; i < rows.length; i++) { if (rows[i].store_id) { distinct[rows[i].store_id] = 1; } }

console.log('SOURCE=interaction_tag');
console.log('SERP_HEADER=' + bkHeader(root));
console.log('RESOLVED_AREA=' + ctx.resolved_area);
console.log('STORE_ID=' + (rows.length ? rows[0].store_id : null) + ' STORE_DISTINCT=' + Object.keys(distinct).length);
console.log('ROW_COUNT=' + rows.length + ' ROW_ERRORS=' + ctx.row_errors);
if (!rows.length) { blocked('no rows from payload'); }
collect(rows);
