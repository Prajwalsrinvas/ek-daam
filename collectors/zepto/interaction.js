const keyword = input.keyword;
const pincode = input.pincode;

if (!keyword) { bad_input('missing keyword'); }
if (!pincode) { bad_input('missing pincode'); }

tag_response('search_api', /user-search-service\/api\/v3\/search(?!\/filters)/);
tag_response('search_api_any', /user-search-service\/api\/v3\/search/);

country('in');
console.log('B12 ROUTING=country-in tag_response-only');
console.log('KEYWORD=' + keyword + ' PINCODE=' + pincode);
navigate('https://www.zepto.com/search?query=' + encodeURIComponent(keyword));

const hyd1 = el_exists('[data-slot-id]', 25000);
console.log('HYDRATION_MARKER1=' + hyd1);

let locBtn = null;
if (el_exists('[data-testid*="address" i]', 12000)) { locBtn = '[data-testid*="address" i]'; }
if (locBtn === null && el_exists('[data-testid*="location" i]', 2500)) { locBtn = '[data-testid*="location" i]'; }
if (locBtn === null && el_exists('button[aria-label*="address" i]', 2500)) { locBtn = 'button[aria-label*="address" i]'; }
if (locBtn === null && el_exists('button[aria-label*="location" i]', 2500)) { locBtn = 'button[aria-label*="location" i]'; }
if (locBtn === null && el_exists('header button', 2500)) { locBtn = 'header button'; }
console.log('SEL location_button=' + locBtn);
if (locBtn === null) { blocked('location button not found'); }
click(locBtn);
wait_page_idle();

let pinInput = null;
if (el_exists('input[placeholder*="address" i]', 15000)) { pinInput = 'input[placeholder*="address" i]'; }
if (pinInput === null && el_exists('input[placeholder*="pincode" i]', 2500)) { pinInput = 'input[placeholder*="pincode" i]'; }
if (pinInput === null && el_exists('input[placeholder*="location" i]', 2500)) { pinInput = 'input[placeholder*="location" i]'; }
if (pinInput === null && el_exists('input[placeholder*="area" i]', 2500)) { pinInput = 'input[placeholder*="area" i]'; }
console.log('SEL pincode_input=' + pinInput);
if (pinInput === null) { blocked('address dialog input not found'); }
type(pinInput, pincode, {replace: true});
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /faro\./, /events\.zepto/]});

const hSug = html() || '';
let anchorIdx = hSug.indexOf('address-search-container');
if (anchorIdx === -1) { anchorIdx = hSug.indexOf('Search a new address'); }
const dumpAll = (anchorIdx > -1) ? hSug.slice(anchorIdx, anchorIdx + 1200).replace(/\s+/g, ' ') : 'ANCHOR_NOT_FOUND';
console.log('SUGG_DOM_A=' + dumpAll);

let sugg = null;
if (el_exists('[data-testid="address-search-item"] ~ [data-testid="address-search-item"]', 15000)) { sugg = '[data-testid="address-search-item"] ~ [data-testid="address-search-item"]'; }
if (sugg === null) { console.log('RETYPE_PINCODE'); type(pinInput, pincode, {replace: true}); wait_network_idle({timeout: 1500, ignore: [/^blob:/, /faro\./, /events\.zepto/]}); }
if (sugg === null && el_exists('[data-testid="address-search-item"] ~ [data-testid="address-search-item"]', 15000)) { sugg = '[data-testid="address-search-item"] ~ [data-testid="address-search-item"]'; }
if (sugg === null && el_exists('[data-testid="address-search-item"]', 4000)) { sugg = '[data-testid="address-search-item"]'; }
console.log('SEL suggestion=' + sugg);
if (sugg === null) { const hF = html() || ''; const aF = hF.indexOf('address-search-container'); console.log('SUGG_DOM_FAIL=' + (aF > -1 ? hF.slice(aF, aF + 600).replace(/\s+/g, ' ') : 'CONTAINER_ABSENT')); }
if (sugg === null) { blocked('no address suggestions rendered'); }
click(sugg);
wait_page_idle();
wait_hidden('input[placeholder*="address" i]', {timeout: 20000});
console.log('DIALOG_CLOSED=true');
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /faro\./, /events\.zepto/]});

const hLoc = html() || '';
const areaMatch = hLoc.match(new RegExp('[^<>]{0,80}' + pincode + '[^<>]{0,80}'));
console.log('AREA_TEXT_AFTER_CHOREO=' + (areaMatch ? areaMatch[0].trim().slice(0, 120) : 'NOT_FOUND'));

const hyd2 = el_exists('[data-slot-id]', 15000);
console.log('HYDRATION_MARKER2=' + hyd2);
wait_network_idle({timeout: 1500, ignore: [/^blob:/, /faro\./, /events\.zepto/]});
try { wait_for_parser_value('search_api', {timeout: 15000}); console.log('WFPV=ok'); } catch (eW) { console.log('WFPV=timeout'); }

let out = parse();
console.log('POST_BIND SA_URL=' + out.search_api_url);

let bodyObj = out.search_api;
if (bodyObj === undefined || bodyObj === null) { bodyObj = out.search_api_any; }
if (typeof bodyObj === 'string') { try { bodyObj = JSON.parse(bodyObj); } catch (eB) { bodyObj = null; } }
let lay = (bodyObj && bodyObj.layout) || [];
console.log('LAYOUT_WIDGETS=' + lay.length);

if (!lay.length) { console.log('REFRESH_NAVIGATE'); navigate('https://www.zepto.com/search?query=' + encodeURIComponent(keyword)); }
if (!lay.length) { const hyd3 = el_exists('[data-slot-id]', 25000); console.log('HYDRATION_MARKER3=' + hyd3); wait_network_idle({timeout: 1500, ignore: [/^blob:/, /faro\./, /events\.zepto/]}); out = parse(); bodyObj = out.search_api; if (bodyObj === undefined || bodyObj === null) { bodyObj = out.search_api_any; } if (typeof bodyObj === 'string') { try { bodyObj = JSON.parse(bodyObj); } catch (eB2) { bodyObj = null; } } lay = (bodyObj && bodyObj.layout) || []; console.log('LAYOUT_WIDGETS2=' + lay.length); }
if (!lay.length) { blocked('no search payload captured after bind'); }

tag_screenshot('serp_screenshot', {full_page: false});
const outS = parse();
const shotVal = outS.serp_screenshot_url || outS.serp_screenshot || null;
console.log('SHOT_TYPE=' + (typeof shotVal) + ' SHOT=' + String(shotVal).slice(0, 90));

const CDN2 = 'https://cdn.zeptonow.com/production/';
const capAt = new Date().toISOString();
const rows2 = [];
const seen2 = {};
for (let w of lay) { if (!w || (w.widgetId !== 'PRODUCT_GRID' && w.widgetId !== 'PREMIUM_CONTEXTUAL_ADS_V2')) { continue; } const isAd = (w.widgetId === 'PREMIUM_CONTEXTUAL_ADS_V2'); const items = (w.data && w.data.resolver && w.data.resolver.data && w.data.resolver.data.items) || []; for (let it of items) { const pr = it && it.productResponse; if (!pr || !pr.product || pr.sellingPrice === undefined || pr.sellingPrice === null) { continue; } const pv = pr.productVariant || {}; const pid = pv.productId || pr.id; if (!pid || seen2[pid]) { continue; } seen2[pid] = 1; const rs = pv.ratingSummary || {}; const ipath = (pv.images && pv.images[0] && pv.images[0].path) ? CDN2 + pv.images[0].path : null; let mrpV = null; if (pr.mrp !== undefined && pr.mrp !== null) { try { mrpV = new Money(pr.mrp / 100, 'INR'); } catch (eM1) { mrpV = pr.mrp / 100; } } let spV = null; if (pr.sellingPrice !== undefined && pr.sellingPrice !== null) { try { spV = new Money(pr.sellingPrice / 100, 'INR'); } catch (eM2) { spV = pr.sellingPrice / 100; } } let dspV = null; if (pr.discountedSellingPrice !== undefined && pr.discountedSellingPrice !== null) { try { dspV = new Money(pr.discountedSellingPrice / 100, 'INR'); } catch (eM3) { dspV = pr.discountedSellingPrice / 100; } } rows2.push({ product_name: (pr.product && pr.product.name) || null, brand: (pr.product && pr.product.brand) || null, package_size: pv.formattedPacksize || null, product_id: pid, mrp: mrpV, selling_price: spV, discounted_selling_price: dspV, out_of_stock: !!pr.outOfStock, available_quantity: pr.availableQuantity === undefined ? null : pr.availableQuantity, is_sponsored: isAd, rating: rs.averageRating === undefined ? null : rs.averageRating, image_url: ipath, serp_screenshot: shotVal, store_id: pr.storeId || null, requested_pincode: pincode, resolved_area: outS.resolved_area || null, eta_minutes: outS.eta_minutes === undefined ? null : outS.eta_minutes, captured_at: capAt }); } }

const finalStore = rows2.length ? rows2[0].store_id : null;
console.log('SOURCE=interaction_tag');
console.log('RESOLVED_AREA=' + outS.resolved_area);
console.log('STORE_ID=' + finalStore);
console.log('ROW_COUNT=' + rows2.length);
if (!rows2.length) { blocked('no rows from payload'); }
collect(rows2);
