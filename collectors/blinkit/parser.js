// BLINKIT v1 parser — THIN, B12/zepto-unified-parser style. It deliberately does NOT read
// `parser.<tag>` for data: tag values never populate there live. Its
// only job is to hand the interaction cheap DOM facts; parse()'s RETURN carries these keys
// PLUS the auto-injected tag fields (search_api, search_api_any, serp_screenshot + _url).
// Never name a returned key after a tag field — the injection overwrites same-named keys.
let dbg_probe = 'ok';
try { new Money(1, 'INR'); } catch (eP1) { dbg_probe = 'MONEY_FAIL'; }
try { new Image('https://x/y.png'); } catch (eP2) { dbg_probe = dbg_probe + '|IMAGE_FAIL'; }
// Diagnostic only — kept to keep proving that on Blinkit. Nothing below depends on it.
let sa_sniff = 'undef';
try { const sa0 = parser.search_api; if (sa0 === null) { sa_sniff = 'null'; } else if (sa0 !== undefined) { sa_sniff = (typeof sa0) + ':' + (typeof sa0 === 'string' ? sa0.slice(0, 40) : Object.keys(sa0 || {}).slice(0, 6).join(',')); } } catch (eS0) { sa_sniff = 'SNIFF_ERR'; }

let body_text = '';
try { body_text = $('body').text() || ''; } catch (eB) { body_text = ''; }
// resolved_area = the location header text around the requested pincode.
let resolved_area = null;
try { const pc = input.pincode; if (pc) { const m = body_text.match(new RegExp(pc + '[\\s\\S]{0,90}')); if (m) { resolved_area = m[0].replace(/\s+/g, ' ').trim().slice(0, 120); } } } catch (eR) { resolved_area = null; }
// page-level ETA ("Delivery in 8 minutes"); per-row eta badges override this in the row build.
let page_eta = null;
try { const mE = body_text.match(/(\d+)\s*min/i); if (mE) { page_eta = parseInt(mE[1], 10); } } catch (eE) { page_eta = null; }

// Hydration / control marker counts. These are what the diag run reads to confirm or replace
// every UNVERIFIED selector in the interaction.
const cnt = (sel) => { try { const n = $(sel); return (n && n.length) ? n.length : 0; } catch (eC) { return 0; } };
const n_plp = cnt('[data-test-id="plp-product"]');
const n_plp_alt = cnt('[data-testid="plp-product"]');
const n_class_product = cnt('[class*="Product"]');
const n_locationbar = cnt('[class*="LocationBar"]');
const n_locationlist = cnt('[class*="LocationSearchList"]');
let html_len = 0;
let n_card_snippet = 0;
let n_cart_item = 0;
let n_merchant_id = 0;
try { const whole = $.html() || ''; html_len = whole.length; n_card_snippet = whole.split('product_card_snippet_type_2').length - 1; n_cart_item = whole.split('cart_item').length - 1; n_merchant_id = whole.split('merchant_id').length - 1; } catch (eW) { }

return { resolved_area: resolved_area, page_eta: page_eta, n_plp: n_plp, n_plp_alt: n_plp_alt, n_class_product: n_class_product, n_locationbar: n_locationbar, n_locationlist: n_locationlist, html_len: html_len, n_card_snippet: n_card_snippet, n_cart_item: n_cart_item, n_merchant_id: n_merchant_id, dbg_probe: dbg_probe, sa_sniff: sa_sniff };
