// CHAOS parser. Every field is read off the rendered page: the store publishes
// no JSON copy of its catalogue, so a redesign really does break this file.
//
// Selectors below describe store version v1: `.product-card` tiles with
// `.product-title` / `.product-brand` / `.pack-size` / `.price-now` /
// `.price-mrp` / `.stock-state` / `.sponsored-tag`, and a `#delivery-area` line
// carrying the store's own delivering-to text.
const money = (v) => { if (v === null || v === undefined) { return null; } try { return new Money(v, 'INR'); } catch (eM) { return v; } };
const img = (u) => { if (!u) { return null; } try { return new Image(u); } catch (eI) { return u; } };
const t = (el) => { if (!el || !el.length) { return null; } try { const s = el.text_sane(); return s ? s : null; } catch (eT) { const s2 = (el.text() || '').trim(); return s2 ? s2 : null; } };
const rupees = (raw) => { if (!raw) { return null; } const m = String(raw).replace(/,/g, '').match(/(\d+(?:\.\d+)?)/); return m ? parseFloat(m[1]) : null; };
const whole = (raw) => { if (raw === null || raw === undefined || raw === '') { return null; } const m = String(raw).match(/(\d+)/); return m ? parseInt(m[1], 10) : null; };

const areaEl = $('#delivery-area').first();
const resolved_area = t(areaEl);
const store_id_seen = (areaEl && areaEl.length) ? (areaEl.attr('data-hub') || null) : null;
const eta_minutes = whole(t($('.eta-value').first()));

let row_errors = 0;
const seen = {};
const products = [];

$('.product-card').each(function () {
  try {
    const card = $(this);
    const product_id = card.attr('data-product-id') || null;
    if (!product_id || seen[product_id]) { return; }
    const product_name = t(card.find('.product-title').first());
    if (!product_name) { return; }
    seen[product_id] = 1;

    const stock = card.find('.stock-state').first();
    const in_stock = stock.length ? (stock.attr('data-in-stock') === 'true') : true;
    const availableRaw = stock.length ? stock.attr('data-available') : null;
    const rating = t(card.find('.rating-value').first());

    products.push({
      product_name: product_name,
      brand: t(card.find('.product-brand').first()),
      package_size: t(card.find('.pack-size').first()),
      product_id: product_id,
      mrp: money(rupees(t(card.find('.price-mrp').first()))),
      selling_price: money(rupees(t(card.find('.price-now').first()))),
      discounted_selling_price: null,
      out_of_stock: !in_stock,
      available_quantity: whole(availableRaw),
      is_sponsored: card.find('.sponsored-tag').length > 0,
      rating: rating === null ? null : parseFloat(rating),
      image_url: img(card.find('.product-image').first().attr('src') || null)
    });
  } catch (eRow) { row_errors = row_errors + 1; }
});

const listed = $('.results').first().attr('data-result-count') || null;

return {
  products: products,
  resolved_area: resolved_area,
  store_id_seen: store_id_seen,
  eta_minutes: eta_minutes,
  listed_count: whole(listed),
  row_errors: row_errors
};
