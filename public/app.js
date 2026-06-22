const state = {
  site: null,
  products: [],
  homeFilter: "all",
  catalogFilter: "all",
  catalogSearch: "",
  catalogSort: "featured",
  catalogStock: "all",
  activeProductId: null,
  admin: {
    dashboard: null,
    products: [],
    orders: [],
    contacts: [],
    imageData: "",
    homeImageData: "",
    ordersSort: "created-desc",
    contactsSort: "created-desc",
  },
};

const productFilters = [
  { key: "all", label: "Tous" },
  { key: "neuf", label: "Neuf" },
  { key: "occasion", label: "Occasion" },
  { key: "ressort", label: "Avec ressort" },
  { key: "sans ressort", label: "Sans ressort" },
  { key: "1 place", label: "1 place" },
  { key: "2 places", label: "2 places" },
  { key: "4 places", label: "4 places" },
];

async function apiFetch(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "include",
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Une erreur est survenue.");
  return data;
}

function showToast(message, isError = false) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.style.background = isError ? "var(--danger)" : "var(--ink)";
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 2800);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function normalizeValue(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function formatRating(product) {
  return product.review_count > 0
    ? `${product.rating}/5 — ${product.review_count} avis`
    : "Pas encore d'avis";
}

function stockClassName(stockStatus) {
  const normalized = normalizeValue(stockStatus);
  if (normalized.includes("limite")) return "Stock limité";
  if (normalized.includes("stock")) return "En stock";
  if (normalized.includes("commande")) return "Sur commande";
  return stockStatus;
}

function navigateTo(pageId) {
  document.querySelectorAll(".page").forEach((page) => page.classList.remove("active"));
  document.querySelectorAll(".nav-link").forEach((button) => button.classList.remove("active"));
  const target = document.getElementById("page-" + pageId);
  if (target) target.classList.add("active");
  document.querySelectorAll(".nav-link[data-page='" + pageId + "']").forEach((button) => button.classList.add("active"));
  document.getElementById("main-nav").classList.remove("open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
}

function productMatchesFilter(product, filter) {
  if (filter === "all") return true;
  return [product.category, product.mattress_type, product.size_label].includes(filter);
}

function productMatchesSearch(product, search) {
  if (!search) return true;
  const haystack = normalizeValue(
    [product.name, product.category, product.mattress_type, product.size_label, product.dimensions, product.description].join(" ")
  );
  return haystack.includes(normalizeValue(search));
}

function productMatchesStock(product, stock) {
  if (stock === "all") return true;
  return normalizeValue(stockClassName(product.stock_status)) === normalizeValue(stock);
}

function sortProducts(products, sortKey) {
  const sorted = [...products];
  switch (sortKey) {
    case "price-asc": return sorted.sort((a, b) => a.price_gnf - b.price_gnf);
    case "price-desc": return sorted.sort((a, b) => b.price_gnf - a.price_gnf);
    case "rating-desc": return sorted.sort((a, b) => b.rating - a.rating || b.review_count - a.review_count);
    case "newest": return sorted.sort((a, b) => b.id - a.id);
    default: return sorted.sort((a, b) => Number(b.featured) - Number(a.featured) || b.id - a.id);
  }
}

function getCatalogProducts() {
  return sortProducts(
    state.products
      .filter((p) => productMatchesFilter(p, state.catalogFilter))
      .filter((p) => productMatchesSearch(p, state.catalogSearch))
      .filter((p) => productMatchesStock(p, state.catalogStock)),
    state.catalogSort
  );
}

function renderFilterRow(containerId, activeFilter, handler) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = productFilters
    .map((item) =>
      `<button class="filter-btn ${item.key === activeFilter ? "active" : ""}" data-filter="${escapeHtml(item.key)}">${escapeHtml(item.label)}</button>`
    )
    .join("");
  container.querySelectorAll(".filter-btn").forEach((button) => {
    button.addEventListener("click", () => handler(button.dataset.filter));
  });
}

function productCard(product) {
  const image = product.image_url
    ? `<div class="product-image"><img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.name)}"></div>`
    : `<div class="product-image"><div class="product-placeholder">🛏️</div></div>`;
  return `
    <article class="product-card">
      ${image}
      <div class="product-content">
        <div class="badge-row">
          <span class="badge">${escapeHtml(product.category)}</span>
          <span class="badge">${escapeHtml(stockClassName(product.stock_status))}</span>
        </div>
        <h3>${escapeHtml(product.name)}</h3>
        <div class="meta-row">
          <span class="meta">${escapeHtml(product.size_label)}</span>
          <span class="meta">${escapeHtml(product.mattress_type)}</span>
          <span class="meta">${escapeHtml(product.dimensions)}</span>
        </div>
        <p class="muted">${escapeHtml(product.description)}</p>
        <div class="product-actions">
          <div class="price">${escapeHtml(product.formatted_price)}</div>
          <div class="button-row">
            <button class="btn-linkish detail-btn" data-product-id="${product.id}">Voir détails</button>
            <button class="btn btn-primary order-btn" data-product-id="${product.id}">Commander</button>
          </div>
        </div>
      </div>
    </article>
  `;
}

function renderProducts() {
  const homeProducts = state.products
    .filter((p) => p.featured)
    .filter((p) => productMatchesFilter(p, state.homeFilter));
  const catalogProducts = getCatalogProducts();

  const homeEl = document.getElementById("home-products");
  const catalogEl = document.getElementById("catalog-products");
  const countEl = document.getElementById("catalog-count");

  if (homeEl) homeEl.innerHTML = homeProducts.map(productCard).join("") || `<p class="muted">Aucun produit mis en avant.</p>`;
  if (catalogEl) catalogEl.innerHTML = catalogProducts.map(productCard).join("") || `<div class="panel"><h3>Aucun matelas trouvé</h3></div>`;
  if (countEl) countEl.textContent = `${catalogProducts.length} produit${catalogProducts.length > 1 ? "s" : ""}`;

  document.querySelectorAll(".order-btn").forEach((btn) =>
    btn.addEventListener("click", () => openOrderModal(Number(btn.dataset.productId)))
  );
  document.querySelectorAll(".detail-btn").forEach((btn) =>
    btn.addEventListener("click", () => openProductModal(Number(btn.dataset.productId)))
  );
}

function renderHeroStats() {
  const el = document.getElementById("hero-stats");
  if (!el) return;
  el.innerHTML = [
    { label: "Produits actifs", value: String(state.products.length) },
    { label: "Choix", value: "Neuf + Occasion" },
    { label: "Zone couverte", value: state.site?.delivery_area || "Guinée" },
  ]
    .map((item) => `<div class="stat-box"><strong>${escapeHtml(item.value)}</strong><span>${escapeHtml(item.label)}</span></div>`)
    .join("");
}

function renderHomeBackgroundPreview() {
  const preview = document.getElementById("home-background-preview");
  if (!preview) return;
  const imageUrl = state.admin.homeImageData || state.site?.home_background_url || "";
  preview.innerHTML = imageUrl
    ? `<img src="${escapeHtml(imageUrl)}" alt="Fond de la page d'accueil">`
    : `<span>Aucune image de fond</span>`;
}

function applySiteInfo() {
  if (!state.site) return;
  const hero = document.getElementById("home-hero");
  const subEl = document.getElementById("hero-subheadline");
  const deliveryEl = document.getElementById("delivery-text");
  const aboutEl = document.getElementById("about-body");
  if (subEl) subEl.textContent = state.site.subheadline;
  if (deliveryEl) deliveryEl.textContent = "Livraison " + state.site.delivery_area;
  if (aboutEl) aboutEl.textContent = state.site.about.body;
  if (hero) {
    if (state.site.home_background_url) {
      hero.style.setProperty("--hero-background-image", `url("${state.site.home_background_url}")`);
      hero.classList.add("hero-with-cover");
    } else {
      hero.style.removeProperty("--hero-background-image");
      hero.classList.remove("hero-with-cover");
    }
  }
  [["phone-link", state.site.phone_primary], ["contact-phone-primary", state.site.phone_primary], ["contact-phone-secondary", state.site.phone_secondary]].forEach(([id, phone]) => {
    const el = document.getElementById(id);
    if (el) { el.textContent = phone; el.href = "tel:" + phone.replace(/\s+/g, ""); }
  });
  ["whatsapp-link", "hero-whatsapp", "contact-band-whatsapp", "contact-wa-primary"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.href = "https://wa.me/" + state.site.whatsapp_number;
  });
  const callEl = document.getElementById("contact-band-call");
  if (callEl) callEl.href = "tel:" + state.site.phone_secondary.replace(/\s+/g, "");
  renderHomeBackgroundPreview();
}

async function loadPublicData() {
  const [siteData, productsData] = await Promise.all([apiFetch("/api/public/site"), apiFetch("/api/public/products")]);
  state.site = siteData;
  state.products = productsData.products;
  applySiteInfo();
  renderHeroStats();
  renderFilterRow("home-filters", state.homeFilter, (filter) => { state.homeFilter = filter; renderProducts(); });
  renderFilterRow("catalog-filters", state.catalogFilter, (filter) => { state.catalogFilter = filter; renderProducts(); });
  syncCatalogControls();
  renderProducts();
}

function syncCatalogControls() {
  const searchInput = document.getElementById("catalog-search");
  const sortInput = document.getElementById("catalog-sort");
  const stockInput = document.getElementById("catalog-stock");
  if (searchInput) searchInput.value = state.catalogSearch;
  if (sortInput) sortInput.value = state.catalogSort;
  if (stockInput) stockInput.value = state.catalogStock;
}

function getProductById(productId) {
  return state.products.find((item) => item.id === productId);
}

function productWhatsappUrl(product) {
  const phone = state.site?.whatsapp_number || "224610492345";
  const message = encodeURIComponent(`Bonjour, je veux des détails sur ${product.name} (${product.formatted_price}).`);
  return `https://wa.me/${phone}?text=${message}`;
}

function openProductModal(productId) {
  const product = getProductById(productId);
  if (!product) return;
  state.activeProductId = productId;
  const visual = product.image_url
    ? `<div class="product-detail-visual"><img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.name)}"></div>`
    : `<div class="product-detail-visual"><div class="product-placeholder">🛏️</div></div>`;
  document.getElementById("product-detail").innerHTML = `
    ${visual}
    <div class="detail-copy">
      <div class="detail-header">
        <span class="badge">${escapeHtml(product.category)}</span>
        <span class="badge">${escapeHtml(stockClassName(product.stock_status))}</span>
        ${product.featured ? '<span class="badge">Sélection DjibShop</span>' : ""}
      </div>
      <h3>${escapeHtml(product.name)}</h3>
      <p class="price">${escapeHtml(product.formatted_price)}</p>
      <p class="muted">${escapeHtml(formatRating(product))}</p>
      <div class="detail-meta-grid">
        <div class="detail-meta-item"><strong>Taille</strong><span>${escapeHtml(product.size_label)}</span></div>
        <div class="detail-meta-item"><strong>Type</strong><span>${escapeHtml(product.mattress_type)}</span></div>
        <div class="detail-meta-item"><strong>Dimensions</strong><span>${escapeHtml(product.dimensions)}</span></div>
      </div>
      <p>${escapeHtml(product.description)}</p>
      <div class="detail-note">Ce produit peut être réservé rapidement depuis WhatsApp ou commandé ici.</div>
      <div class="detail-cta-row">
        <button class="btn btn-primary" id="detail-order-btn">Commander maintenant</button>
        <a class="btn btn-secondary" href="${productWhatsappUrl(product)}" target="_blank" rel="noopener noreferrer">WhatsApp</a>
      </div>
    </div>
  `;
  document.getElementById("detail-order-btn").addEventListener("click", () => {
    closeProductModal();
    openOrderModal(product.id);
  });
  document.getElementById("product-modal").classList.remove("hidden");
}

function closeProductModal() {
  document.getElementById("product-modal").classList.add("hidden");
}

function openOrderModal(productId) {
  const product = getProductById(productId);
  if (!product) return;
  state.activeProductId = productId;
  document.querySelector('#order-form [name="product_id"]').value = product.id;
  document.getElementById("order-product-summary").textContent =
    `${product.name} — ${product.formatted_price} — ${product.dimensions} — ${stockClassName(product.stock_status)}`;
  document.getElementById("order-modal").classList.remove("hidden");
}

function closeOrderModal() {
  document.getElementById("order-modal").classList.add("hidden");
}

async function submitOrder(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  payload.product_id = Number(payload.product_id);
  try {
    const result = await apiFetch("/api/public/orders", { method: "POST", body: JSON.stringify(payload) });
    showToast(result.message || `Commande enregistrée. Référence #${result.order_id}`);
    form.reset();
    closeOrderModal();
    await refreshAdminDataIfOpen();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function submitContact(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const result = await apiFetch("/api/public/contact", { method: "POST", body: JSON.stringify(payload) });
    showToast(result.message || "Message envoyé avec succès.");
    form.reset();
    await refreshAdminDataIfOpen();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function adminLogin(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    await apiFetch("/api/admin/login", { method: "POST", body: JSON.stringify(payload) });
    await loadAdminData();
    navigateTo("admin");
    showToast("Connexion réussie.");
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderMetrics() {
  const metrics = state.admin.dashboard?.metrics;
  if (!metrics) return;
  document.getElementById("metrics").innerHTML = [
    ["Produits", metrics.products],
    ["Commandes", metrics.orders],
    ["En attente", metrics.pending_orders],
    ["Livrées", metrics.delivered_orders],
    ["Messages", metrics.contacts],
  ]
    .map(([label, value]) =>
      `<article class="metric-card"><span class="muted">${escapeHtml(label)}</span><strong>${escapeHtml(String(value))}</strong></article>`
    )
    .join("");
}

function renderAdminProducts() {
  document.getElementById("admin-products-table").innerHTML =
    state.admin.products
      .map((product) => `
    <tr>
      <td><strong>${escapeHtml(product.name)}</strong><br><span class="muted">${escapeHtml(product.dimensions)}</span></td>
      <td>${escapeHtml(product.category)}</td>
      <td>${escapeHtml(product.mattress_type)}</td>
      <td>${escapeHtml(product.size_label)}</td>
      <td>${escapeHtml(product.formatted_price)}</td>
      <td>${escapeHtml(product.stock_status)}</td>
      <td>
        <div class="button-row">
          <button class="btn btn-secondary edit-product-btn" data-product-id="${product.id}">Modifier</button>
          <button class="btn btn-secondary delete-product-btn" data-product-id="${product.id}">Supprimer</button>
        </div>
      </td>
    </tr>
  `)
      .join("") || `<tr><td colspan="7">Aucun produit.</td></tr>`;
  document.querySelectorAll(".edit-product-btn").forEach((btn) =>
    btn.addEventListener("click", () => populateProductForm(Number(btn.dataset.productId)))
  );
  document.querySelectorAll(".delete-product-btn").forEach((btn) =>
    btn.addEventListener("click", () => removeProduct(Number(btn.dataset.productId)))
  );
}

function renderAdminOrders() {
  const options = [
    ["attente", "En attente"],
    ["confirmee", "Confirmée"],
    ["livraison", "En livraison"],
    ["livree", "Livrée"],
    ["annulee", "Annulée"],
  ];
  document.getElementById("admin-orders-table").innerHTML =
    state.admin.orders
      .map((order) => `
    <tr>
      <td><strong>${escapeHtml(order.customer_name)}</strong><br><span class="muted">${escapeHtml(order.phone)}</span></td>
      <td>${escapeHtml(order.product_name)}</td>
      <td>${escapeHtml(order.city)}</td>
      <td>${escapeHtml(order.payment_method)}</td>
      <td>
        <select class="status-select" data-order-id="${order.id}">
          ${options.map(([value, label]) => `<option value="${value}" ${order.status === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </td>
      <td>${escapeHtml(formatDate(order.created_at))}</td>
      <td><button class="btn btn-secondary delete-order-btn" data-order-id="${order.id}">🗑️</button></td>
    </tr>
  `)
      .join("") || `<tr><td colspan="7">Aucune commande.</td></tr>`;

  document.querySelectorAll(".delete-order-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Supprimer cette commande ?")) return;
      try {
        await apiFetch(`/api/admin/orders/${btn.dataset.orderId}`, { method: "DELETE" });
        state.admin.orders = state.admin.orders.filter((o) => o.id !== Number(btn.dataset.orderId));
        renderAdminOrders();
        showToast("Commande supprimée.");
      } catch (error) {
        showToast(error.message, true);
      }
    })
  );

  document.querySelectorAll(".status-select").forEach((select) =>
    select.addEventListener("change", async () => {
      try {
        await apiFetch(`/api/admin/orders/${select.dataset.orderId}`, {
          method: "PUT",
          body: JSON.stringify({ status: select.value }),
        });
        showToast("Statut mis à jour.");
        await loadAdminData();
      } catch (error) {
        showToast(error.message, true);
      }
    })
  );
}

function renderAdminContacts() {
  document.getElementById("admin-contacts-list").innerHTML =
    state.admin.contacts
      .map((contact) => `
    <article class="message-item">
      <strong>${escapeHtml(contact.full_name)}</strong>
      <p class="muted">${escapeHtml(contact.phone)} · ${escapeHtml(formatDate(contact.created_at))}</p>
      <p>${escapeHtml(contact.message)}</p>
      <button class="btn btn-secondary delete-contact-btn" data-contact-id="${contact.id}" style="margin-top:10px">🗑️ Supprimer</button>
    </article>
  `)
      .join("") || `<p class="muted">Aucun message.</p>`;

  document.querySelectorAll(".delete-contact-btn").forEach((btn) =>
    btn.addEventListener("click", async () => {
      if (!confirm("Supprimer ce message ?")) return;
      try {
        await apiFetch(`/api/admin/contacts/${btn.dataset.contactId}`, { method: "DELETE" });
        state.admin.contacts = state.admin.contacts.filter((c) => c.id !== Number(btn.dataset.contactId));
        renderAdminContacts();
        showToast("Message supprimé.");
      } catch (error) {
        showToast(error.message, true);
      }
    })
  );
}

function setAdminVisible(visible) {
  document.getElementById("admin-login-panel").classList.toggle("hidden", visible);
  document.getElementById("admin-shell").classList.toggle("hidden", !visible);
}

async function loadAdminData({ silent = false } = {}) {
  try {
    const [dashboard, products, orders, contacts] = await Promise.all([
      apiFetch("/api/admin/dashboard"),
      apiFetch("/api/admin/products"),
      apiFetch("/api/admin/orders"),
      apiFetch("/api/admin/contacts"),
    ]);
    state.admin.dashboard = dashboard;
    state.admin.products = products.products;
    state.admin.orders = orders.orders;
    state.admin.contacts = contacts.contacts;
    setAdminVisible(true);
    renderMetrics();
    renderAdminProducts();
    renderAdminOrders();
    renderAdminContacts();
    renderHomeBackgroundPreview();
  } catch (error) {
    setAdminVisible(false);
    if (!silent) throw error;
  }
}

async function refreshAdminDataIfOpen() {
  if (!document.getElementById("admin-shell").classList.contains("hidden")) {
    await loadAdminData();
  }
}

function switchAdminTab(tab) {
  document.querySelectorAll(".tab-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.adminTab === tab));
  document.querySelectorAll("#page-admin .admin-tab").forEach((section) =>
    section.classList.toggle("active", section.id === "admin-tab-" + tab)
  );
}

function resetProductForm() {
  state.admin.imageData = "";
  const form = document.getElementById("product-form");
  form.reset();
  form.querySelector('[name="id"]').value = "";
  form.querySelector('[name="rating"]').value = "0";
  form.querySelector('[name="review_count"]').value = "0";
  document.getElementById("product-form-title").textContent = "Ajouter un produit";
  document.getElementById("product-image-preview").innerHTML = "Aperçu image";
}

function populateProductForm(productId) {
  const product = state.admin.products.find((item) => item.id === productId);
  if (!product) return;
  const form = document.getElementById("product-form");
  form.querySelector('[name="id"]').value = String(product.id);
  form.querySelector('[name="name"]').value = product.name;
  form.querySelector('[name="price_gnf"]').value = product.price_gnf;
  form.querySelector('[name="category"]').value = product.category;
  form.querySelector('[name="mattress_type"]').value = product.mattress_type;
  form.querySelector('[name="size_label"]').value = product.size_label;
  form.querySelector('[name="dimensions"]').value = product.dimensions;
  form.querySelector('[name="rating"]').value = product.rating;
  form.querySelector('[name="review_count"]').value = product.review_count;
  form.querySelector('[name="stock_status"]').value = product.stock_status;
  form.querySelector('[name="featured"]').checked = product.featured;
  form.querySelector('[name="description"]').value = product.description;
  document.getElementById("product-form-title").textContent = "Modifier un produit";
  document.getElementById("product-image-preview").innerHTML = product.image_url
    ? `<img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.name)}">`
    : "Aucune image";
  switchAdminTab("editor");
}

async function removeProduct(productId) {
  if (!confirm("Supprimer ce produit ?")) return;
  try {
    await apiFetch(`/api/admin/products/${productId}`, { method: "DELETE" });
    showToast("Produit supprimé.");
    await Promise.all([loadPublicData(), loadAdminData()]);
  } catch (error) {
    showToast(error.message, true);
  }
}

async function submitProductForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = {
    id: form.querySelector('[name="id"]').value,
    name: form.querySelector('[name="name"]').value.trim(),
    price_gnf: form.querySelector('[name="price_gnf"]').value.trim(),
    category: form.querySelector('[name="category"]').value,
    mattress_type: form.querySelector('[name="mattress_type"]').value,
    size_label: form.querySelector('[name="size_label"]').value,
    dimensions: form.querySelector('[name="dimensions"]').value.trim(),
    rating: form.querySelector('[name="rating"]').value.trim() || 0,
    review_count: form.querySelector('[name="review_count"]').value.trim() || 0,
    stock_status: form.querySelector('[name="stock_status"]').value,
    featured: form.querySelector('[name="featured"]').checked,
    description: form.querySelector('[name="description"]').value.trim(),
    image_data: state.admin.imageData,
  };
  const isEdit = Boolean(payload.id);
  try {
    await apiFetch(isEdit ? `/api/admin/products/${payload.id}` : "/api/admin/products", {
      method: isEdit ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    showToast(isEdit ? "Produit mis à jour." : "Produit ajouté.");
    resetProductForm();
    await Promise.all([loadPublicData(), loadAdminData()]);
    switchAdminTab("products");
  } catch (error) {
    showToast(error.message, true);
  }
}

function bindImageInput() {
  const input = document.querySelector('#product-form [name="image_file"]');
  if (!input) return;
  input.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (!file) {
      state.admin.imageData = "";
      document.getElementById("product-image-preview").innerHTML = "Aperçu image";
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      state.admin.imageData = reader.result;
      document.getElementById("product-image-preview").innerHTML = `<img src="${reader.result}" alt="Aperçu">`;
    };
    reader.readAsDataURL(file);
  });
}

function bindHomeBackgroundInput() {
  const input = document.querySelector('#site-settings-form [name="home_background_file"]');
  if (!input) return;
  input.addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    if (!file) {
      state.admin.homeImageData = "";
      renderHomeBackgroundPreview();
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      state.admin.homeImageData = reader.result;
      renderHomeBackgroundPreview();
    };
    reader.readAsDataURL(file);
  });
}

async function submitSiteSettingsForm(event) {
  event.preventDefault();
  if (!state.admin.homeImageData) {
    showToast("Choisis d'abord une image.", true);
    return;
  }
  try {
    const result = await apiFetch("/api/admin/site", {
      method: "PUT",
      body: JSON.stringify({ image_data: state.admin.homeImageData }),
    });
    state.admin.homeImageData = "";
    state.site = { ...(state.site || {}), home_background_url: result.home_background_url };
    document.getElementById("site-settings-form").reset();
    applySiteInfo();
    showToast(result.message || "Fond mis à jour.");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function clearHomeBackground() {
  try {
    const result = await apiFetch("/api/admin/site", {
      method: "PUT",
      body: JSON.stringify({ clear_background: true }),
    });
    state.admin.homeImageData = "";
    state.site = { ...(state.site || {}), home_background_url: result.home_background_url };
    document.getElementById("site-settings-form").reset();
    applySiteInfo();
    showToast("Fond retiré.");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function logoutAdmin() {
  await apiFetch("/api/admin/logout", { method: "POST", body: "{}" });
  showToast("Déconnecté.");
  setAdminVisible(false);
}

function bindCatalogControls() {
  const searchInput = document.getElementById("catalog-search");
  const sortInput = document.getElementById("catalog-sort");
  const stockInput = document.getElementById("catalog-stock");
  if (searchInput) searchInput.addEventListener("input", (e) => { state.catalogSearch = e.target.value.trim(); renderProducts(); });
  if (sortInput) sortInput.addEventListener("change", (e) => { state.catalogSort = e.target.value; renderProducts(); });
  if (stockInput) stockInput.addEventListener("change", (e) => { state.catalogStock = e.target.value; renderProducts(); });
}

function bindEvents() {
  // Menu switcher boutiques
  const switcherBtn = document.getElementById("shop-switcher-btn");
  const switcherMenu = document.getElementById("shop-switcher-menu");
  if (switcherBtn && switcherMenu) {
    switcherBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      switcherMenu.classList.toggle("hidden");
    });
    document.addEventListener("click", () => switcherMenu.classList.add("hidden"));
  }

  // Navigation
  document.querySelectorAll("[data-page]").forEach((btn) =>
    btn.addEventListener("click", () => navigateTo(btn.dataset.page))
  );

  // Menu mobile
  document.getElementById("menu-toggle").addEventListener("click", () => {
    const nav = document.getElementById("main-nav");
    const isOpen = nav.classList.toggle("open");
    document.getElementById("menu-toggle").setAttribute("aria-expanded", String(isOpen));
  });

  // Modaux
  document.getElementById("close-order-modal").addEventListener("click", closeOrderModal);
  document.getElementById("close-product-modal").addEventListener("click", closeProductModal);
  document.getElementById("order-modal").addEventListener("click", (e) => { if (e.target.id === "order-modal") closeOrderModal(); });
  document.getElementById("product-modal").addEventListener("click", (e) => { if (e.target.id === "product-modal") closeProductModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeOrderModal(); closeProductModal(); } });

  // Formulaires
  document.getElementById("order-form").addEventListener("submit", submitOrder);
  document.getElementById("contact-form").addEventListener("submit", submitContact);
  document.getElementById("admin-login-form").addEventListener("submit", adminLogin);
  document.getElementById("product-form").addEventListener("submit", submitProductForm);
  document.getElementById("site-settings-form").addEventListener("submit", submitSiteSettingsForm);
  document.getElementById("product-reset-btn").addEventListener("click", resetProductForm);
  document.getElementById("clear-home-background-btn").addEventListener("click", clearHomeBackground);
  document.getElementById("admin-logout-btn").addEventListener("click", logoutAdmin);

  // Tabs admin
  document.querySelectorAll(".tab-btn").forEach((btn) =>
    btn.addEventListener("click", () => switchAdminTab(btn.dataset.adminTab))
  );

  bindCatalogControls();
  bindImageInput();
  bindHomeBackgroundInput();
}

async function init() {
  bindEvents();
  await loadPublicData();
  await loadAdminData({ silent: true });
}

document.addEventListener("DOMContentLoaded", () => {
  init().catch((error) => showToast(error.message || "Impossible de charger le site.", true));
});
