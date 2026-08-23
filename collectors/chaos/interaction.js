// CHAOS interaction. Reads the store the app serves at /chaos, which is
// server-rendered HTML with no JSON API behind it, so every field comes out of
// the DOM through parser.js.
//
// Selectors target store version v1 on purpose. When the store is flipped to v2
// the markup changes and this collector stops finding rows, which is the event
// the self-healing pass exists to repair.
//
// Set STORE_BASE to the deployed app's own address before creating the
// collector. The store lives at <host>/chaos/search.
const STORE_BASE = 'https://ekdaam.duckdns.org/chaos/search';

const keyword = input.keyword;
const pincode = input.pincode;

if (!keyword) { bad_input('missing keyword'); }
if (!pincode) { bad_input('missing pincode'); }

country('in');
console.log('KEYWORD=' + keyword + ' PINCODE=' + pincode);

// The URL contract is stable across store versions: a redesign changes the DOM,
// not the address. Both parameters are required for prices to render at all.
const url = STORE_BASE + '?q=' + encodeURIComponent(keyword) + '&pincode=' + encodeURIComponent(pincode);
navigate(url);
wait_page_idle();

// No location, no shelf: the store shows a prompt instead of prices.
if (el_exists('#location-prompt', 2000)) { blocked('store served no location for this pincode'); }

const listed = el_exists('.product-card', 20000);
console.log('CARDS_PRESENT=' + listed);
if (!listed && el_exists('.empty-note', 2000)) { blocked('no products matched this keyword'); }
if (!listed) { blocked('product cards not found'); }

tag_screenshot('serp_screenshot', {full_page: false});

const out = parse();
const shotVal = out.serp_screenshot_url || out.serp_screenshot || null;
console.log('SHOT_TYPE=' + (typeof shotVal) + ' SHOT=' + String(shotVal).slice(0, 90));
console.log('RESOLVED_AREA=' + out.resolved_area);
console.log('STORE_ID=' + out.store_id_seen);
console.log('ETA_MINUTES=' + out.eta_minutes);

const capAt = new Date().toISOString();
const products = out.products || [];
const rows = [];
for (let p of products) {
  rows.push({
    product_name: p.product_name,
    brand: p.brand,
    package_size: p.package_size,
    product_id: p.product_id,
    mrp: p.mrp,
    selling_price: p.selling_price,
    discounted_selling_price: p.discounted_selling_price,
    out_of_stock: p.out_of_stock,
    available_quantity: p.available_quantity,
    is_sponsored: p.is_sponsored,
    rating: p.rating,
    image_url: p.image_url,
    serp_screenshot: shotVal,
    store_id: out.store_id_seen,
    requested_pincode: pincode,
    resolved_area: out.resolved_area,
    eta_minutes: out.eta_minutes,
    captured_at: capAt
  });
}

console.log('ROW_COUNT=' + rows.length);
if (!rows.length) { blocked('no rows parsed from the store page'); }
collect(rows);
