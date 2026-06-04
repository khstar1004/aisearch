const fs = require("fs");
const path = require("path");
const vm = require("vm");

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  contains(name) {
    return this.values.has(name);
  }

  toString() {
    return Array.from(this.values).join(" ");
  }
}

class FakeStyle {
  setProperty(name, value) {
    this[name] = String(value);
  }
}

class FakeElement {
  constructor(tagName, document) {
    this.tagName = tagName.toUpperCase();
    this.document = document;
    this.children = [];
    this.parentNode = null;
    this.attributes = {};
    this.listeners = {};
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = new FakeStyle();
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.title = "";
    this.type = "";
    this.href = "";
    this.src = "";
    this.alt = "";
    this.textContent = "";
  }

  set className(value) {
    this.classList = new FakeClassList();
    String(value || "")
      .split(/\s+/)
      .filter(Boolean)
      .forEach((name) => this.classList.add(name));
  }

  get className() {
    return this.classList.toString();
  }

  set id(value) {
    this.attributes.id = value;
    this.document.ids.set(value, this);
  }

  get id() {
    return this.attributes.id || "";
  }

  set innerHTML(value) {
    this._innerHTML = String(value || "");
    this.children.forEach((child) => {
      child.parentNode = null;
    });
    this.children = [];
    if (this.classList.contains("hai-modal")) {
      buildModalSkeleton(this);
    } else if (this.classList.contains("hai-card")) {
      buildCardSkeleton(this);
    }
  }

  get innerHTML() {
    return this._innerHTML || "";
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    FakeMutationObserver.notify();
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index !== -1) {
      this.children.splice(index, 1);
      child.parentNode = null;
      FakeMutationObserver.notify();
    }
    return child;
  }

  remove() {
    if (this.parentNode) {
      this.parentNode.removeChild(this);
    }
  }

  insertAdjacentElement(position, element) {
    if (position !== "afterend" || !this.parentNode) {
      throw new Error("Only afterend insertion is supported in this check.");
    }
    const index = this.parentNode.children.indexOf(this);
    element.parentNode = this.parentNode;
    this.parentNode.children.splice(index + 1, 0, element);
    FakeMutationObserver.notify();
    return element;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") {
      this.id = value;
    }
    if (name === "class") {
      this.className = value;
    }
    if (name === "data-close") {
      this.dataset.close = String(value);
    }
    if (name === "title") {
      this.title = String(value);
    }
    if (name === "type") {
      this.type = String(value);
    }
  }

  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "class") {
      this.className = "";
    }
    if (name === "data-close") {
      delete this.dataset.close;
    }
  }

  addEventListener(name, callback) {
    const previous = this.listeners[name];
    if (!previous) {
      this.listeners[name] = callback;
      return;
    }
    const callbacks = previous._callbacks || [previous];
    callbacks.push(callback);
    const dispatcher = (event) => callbacks.forEach((item) => item(event));
    dispatcher._callbacks = callbacks;
    this.listeners[name] = dispatcher;
  }

  click() {
    if (this.listeners.click) {
      this.listeners.click({ target: this, preventDefault: () => {} });
    }
  }

  querySelector(selector) {
    assertBrowserSelectorSafe(selector);
    return find(this, selector);
  }

  querySelectorAll(selector) {
    assertBrowserSelectorSafe(selector);
    return findAll(this, selector);
  }

  focus() {
    this.focused = true;
    this.document.activeElement = this;
  }
}

class FakeDocument {
  constructor() {
    this.ids = new Map();
    this.body = new FakeElement("body", this);
    this.head = new FakeElement("head", this);
    this.activeElement = this.body;
    this.readyState = "complete";
    this.listeners = {};
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }

  getElementById(id) {
    return this.ids.get(id) || null;
  }

  querySelector(selector) {
    assertBrowserSelectorSafe(selector);
    return find(this.body, selector) || find(this.head, selector);
  }

  querySelectorAll(selector) {
    assertBrowserSelectorSafe(selector);
    return findAll(this.body, selector).concat(findAll(this.head, selector));
  }

  addEventListener(name, callback) {
    const callbacks = this.listeners[name] || [];
    callbacks.push(callback);
    this.listeners[name] = callbacks;
  }

  dispatchEvent(event) {
    const name = event && event.type ? event.type : String(event || "");
    (this.listeners[name] || []).forEach((callback) => callback(event));
  }
}

class FakeMutationObserver {
  static observers = [];

  constructor(callback) {
    this.callback = callback;
    this.connected = false;
  }

  observe() {
    this.connected = true;
    FakeMutationObserver.observers.push(this);
  }

  disconnect() {
    this.connected = false;
    FakeMutationObserver.observers = FakeMutationObserver.observers.filter((observer) => observer !== this);
  }

  static notify() {
    FakeMutationObserver.observers.slice().forEach((observer) => {
      if (observer.connected) {
        observer.callback([], observer);
      }
    });
  }

  static reset() {
    FakeMutationObserver.observers = [];
  }
}

class FakeFormData {
  constructor() {
    this.fields = [];
  }

  append(name, value) {
    this.fields.push([name, value]);
  }

  get(name) {
    const pair = this.fields.find(([key]) => key === name);
    return pair ? pair[1] : undefined;
  }
}

function buildModalSkeleton(modal) {
  const backdrop = add(modal, "div", "hai-backdrop");
  backdrop.setAttribute("data-close", "true");
  const closeButton = add(modal, "button", "hai-icon-button");
  closeButton.type = "button";
  closeButton.setAttribute("data-close", "true");
  closeButton.setAttribute("aria-label", "닫기");
  const form = add(modal, "form", "hai-search-form");
  const query = add(form, "input", "hai-query");
  query.type = "search";
  const primary = add(form, "button", "hai-primary");
  primary.type = "submit";
  const dropzone = add(form, "div", "hai-dropzone");
  dropzone.setAttribute("tabindex", "0");
  const file = add(dropzone, "input", "hai-file");
  file.type = "file";
  file.setAttribute("accept", "image/jpeg,image/png,image/webp");
  const preview = add(form, "div", "hai-preview");
  preview.hidden = true;
  const previewImage = add(preview, "img", "");
  previewImage.alt = "업로드 이미지 미리보기";
  const removeButton = add(preview, "button", "hai-remove");
  removeButton.type = "button";
  removeButton.textContent = "이미지 삭제";
  add(modal, "div", "hai-error").hidden = true;
  add(modal, "div", "hai-loading").hidden = true;
  add(modal, "div", "hai-notice").hidden = true;
  const categories = add(modal, "section", "hai-categories");
  categories.hidden = true;
  add(categories, "div", "hai-category-list");
  const top = add(modal, "section", "hai-top");
  top.hidden = true;
  add(top, "div", "hai-top-list");
  const items = add(modal, "section", "hai-items");
  items.hidden = true;
  add(items, "div", "hai-item-list");
  const moreWrap = add(items, "div", "hai-more-wrap");
  moreWrap.hidden = true;
  add(moreWrap, "button", "hai-more");
  add(modal, "div", "hai-empty").hidden = true;
}

function buildCardSkeleton(card) {
  card.children = [];
  const imageLink = add(card, "a", "hai-image-link");
  imageLink.href = extractHref(card.innerHTML, "hai-image-link");
  imageLink.setAttribute("aria-label", extractAttr(card.innerHTML, "hai-image-link", "aria-label"));
  const image = add(imageLink, "img", "");
  image.src = extractImageSrc(card.innerHTML);
  image.alt = extractImageAlt(card.innerHTML);
  const body = add(card, "div", "hai-card-body");
  add(body, "div", "hai-score").textContent = extractClassText(card.innerHTML, "hai-score");
  add(body, "div", "hai-product-id").textContent = extractClassText(card.innerHTML, "hai-product-id");
  add(body, "h4", "").textContent = extractTagText(card.innerHTML, "h4");
  add(body, "div", "hai-card-meta").textContent = extractClassText(card.innerHTML, "hai-card-meta");
  add(body, "div", "hai-price").textContent = extractClassText(card.innerHTML, "hai-price");
  const detail = add(body, "a", "hai-detail");
  detail.href = extractHref(card.innerHTML, "hai-detail");
  detail.textContent = extractClassText(card.innerHTML, "hai-detail");
}

function extractHref(html, className) {
  return extractAttr(html, className, "href");
}

function extractAttr(html, className, attrName) {
  const pattern = new RegExp(`class=["'][^"']*${className}[^"']*["'][^>]*href=["']([^"']*)["']`);
  if (attrName === "href") {
    const match = pattern.exec(String(html || ""));
    return match ? match[1] : "";
  }
  const attrPattern = new RegExp(`class=["'][^"']*${className}[^"']*["'][^>]*${attrName}=["']([^"']*)["']`);
  const match = attrPattern.exec(String(html || ""));
  return match ? match[1] : "";
}

function extractImageAlt(html) {
  const match = /<img\b[^>]*alt=["']([^"']*)["']/i.exec(String(html || ""));
  return match ? match[1] : "";
}

function extractImageSrc(html) {
  const match = /<img\b[^>]*src=["']([^"']*)["']/i.exec(String(html || ""));
  return match ? match[1] : "";
}

function extractClassText(html, className) {
  const pattern = new RegExp(`class=["'][^"']*${className}[^"']*["'][^>]*>([^<]*)<`);
  const match = pattern.exec(String(html || ""));
  return match ? match[1] : "";
}

function extractTagText(html, tagName) {
  const pattern = new RegExp(`<${tagName}[^>]*>([^<]*)</${tagName}>`, "i");
  const match = pattern.exec(String(html || ""));
  return match ? match[1] : "";
}

function add(parent, tagName, className) {
  const child = parent.document.createElement(tagName);
  if (className) {
    child.className = className;
  }
  parent.appendChild(child);
  return child;
}

function find(root, selector) {
  return findAll(root, selector)[0] || null;
}

function findAll(root, selector) {
  const trimmed = String(selector || "").trim();
  if (!trimmed) {
    return [];
  }
  if (trimmed.includes(",")) {
    return uniqueElements(trimmed.split(",").flatMap((part) => findAll(root, part.trim())));
  }
  const spaceIndex = trimmed.indexOf(" ");
  if (spaceIndex > 0) {
    const parents = findAll(root, trimmed.slice(0, spaceIndex));
    return uniqueElements(parents.flatMap((parent) => findAll(parent, trimmed.slice(spaceIndex + 1))));
  }
  return walk(root).filter((element) => matches(element, trimmed));
}

function uniqueElements(elements) {
  return elements.filter((element, index) => elements.indexOf(element) === index);
}

function assertBrowserSelectorSafe(selector) {
  const unescapedSelector = String(selector || "").replace(/\\./g, "");
  if (/(^|[\s,>+~])#[^\s,>+~#]+:[^\s,>+~#]+/.test(unescapedSelector)) {
    const error = new Error(`Failed to execute 'querySelector' on 'Document': '${selector}' is not a valid selector.`);
    error.name = "SyntaxError";
    throw error;
  }
}

function matches(element, selector) {
  if (!selector) {
    return false;
  }
  if (selector.includes(",")) {
    return selector.split(",").some((part) => matches(element, part.trim()));
  }
  if (selector.startsWith("#")) {
    return element.id === unescapeCssIdentifier(selector.slice(1));
  }
  if (selector.startsWith(".")) {
    return element.classList.contains(selector.slice(1));
  }
  if (/^button\[type=['"]?submit['"]?\]$/.test(selector)) {
    return element.tagName === "BUTTON" && element.type === "submit";
  }
  if (selector === "a[href]") {
    return element.tagName === "A" && Boolean(element.href);
  }
  if (selector === "[tabindex]") {
    return Object.prototype.hasOwnProperty.call(element.attributes, "tabindex");
  }
  return element.tagName === selector.toUpperCase();
}

function unescapeCssIdentifier(value) {
  return String(value || "").replace(/\\([^0-9A-Fa-f])/g, "$1");
}

function walk(root) {
  return [root, ...root.children.flatMap((child) => walk(child))];
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function buildSearchPage(document, scenario) {
  const form = document.createElement("form");
  form.id = scenario.formId || "searchForm";
  const input = document.createElement("input");
  input.id = scenario.inputId;
  input.type = scenario.inputType || "text";
  if (scenario.inputName) {
    input.setAttribute("name", scenario.inputName);
  }
  if (scenario.inputClass) {
    input.className = scenario.inputClass;
  }
  if (scenario.inputPlaceholder) {
    input.setAttribute("placeholder", scenario.inputPlaceholder);
  }
  input.value = scenario.keyword;
  const submit = document.createElement("button");
  submit.id = scenario.submitId;
  submit.type = "submit";
  submit.textContent = scenario.submitText || "검색";
  form.appendChild(input);
  form.appendChild(submit);
  if (scenario.targetId) {
    const target = document.createElement("span");
    target.id = scenario.targetId;
    form.appendChild(target);
  }
  document.body.appendChild(form);
  return { form, input, submit, target: scenario.targetId ? document.getElementById(scenario.targetId) : null };
}

function finishImageLoad(preview, width = 32, height = 32) {
  const image = preview.querySelector("img");
  image.naturalWidth = width;
  image.naturalHeight = height;
  image.width = width;
  image.height = height;
  if (typeof image.onload === "function") {
    image.onload();
  }
}

function failImageLoad(preview) {
  const image = preview.querySelector("img");
  if (typeof image.onerror === "function") {
    image.onerror();
  }
}

async function runScenario(scenario) {
  const document = new FakeDocument();
  const page = buildSearchPage(document, scenario);
  const fetchCalls = [];
  const beaconCalls = [];
  let searchErrorResponse = null;
  let searchSuccessResponse = null;
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    Blob: class {
      constructor(parts, options) {
        this.parts = parts || [];
        this.type = options && options.type;
      }
    },
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: {
      sendBeacon: (url, blob) => {
        beaconCalls.push({ url, body: (blob.parts || []).join("") });
        return true;
      },
    },
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      if (String(url).endsWith("/api/click-log")) {
        return {
          ok: true,
          json: async () => ({ ok: true }),
        };
      }
      if (searchErrorResponse) {
        return {
          ok: false,
          status: searchErrorResponse.status,
          json: async () => searchErrorResponse.body,
        };
      }
      if (searchSuccessResponse) {
        return {
          ok: true,
          json: async () => searchSuccessResponse,
        };
      }
      return {
        ok: true,
        json: async () => ({
          top: [
            {
              product_id: "P001",
              name: "검정 우산",
              category: "우산",
              price: 8500,
              image_url: "https://images.example.test/p001.jpg",
              product_url: "https://shop.example.test/product/P001",
              score_percent: 91.2,
            },
          ],
          items: [
            {
              product_id: "P002",
              name: "3단 우산",
              category: "우산",
              price: null,
              image_url: "https://images.example.test/p002.jpg",
              product_url: "https://shop.example.test/product/P002",
              score_percent: 82.4,
            },
          ],
          suggested_categories: ["우산", "생활용품"],
          meta: {
            query_type: options.body.get("image") && options.body.get("q") ? "text_image" : (options.body.get("image") ? "image" : "text"),
            offset: options.body.get("offset") || 0,
            has_more: true,
            next_offset: 20,
          },
        }),
      };
    },
  };
  context.window.window = context.window;

  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init(scenario.options);

  const style = document.getElementById("haeorum-ai-search-style");
  assert(style && /grid-template-columns: repeat\(4, minmax\(0, 1fr\)\)/.test(style.textContent), `${scenario.name}: desktop related-products grid should use four columns.`);
  assert(/\.hai-top-list \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}/.test(style.textContent), `${scenario.name}: top results grid should reserve three columns.`);
  assert(/@media \(max-width: 560px\)[\s\S]*\.hai-row, \.hai-top-list, \.hai-item-list \{ grid-template-columns: 1fr; \}/.test(style.textContent), `${scenario.name}: narrow mobile grid should collapse to one column.`);
  assert(/@media \(min-width: 561px\) and \(max-width: 860px\)[\s\S]*\.hai-top-list, \.hai-item-list \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/.test(style.textContent), `${scenario.name}: wide mobile grid should use two columns.`);
  assert(/@media \(min-width: 861px\) and \(max-width: 1120px\)[\s\S]*\.hai-item-list \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); \}/.test(style.textContent), `${scenario.name}: tablet grid should use three related-product columns.`);

  const root = scenario.targetId ? page.target : document.body.querySelector(".hai-root");
  const trigger = root.children[0];
  const rootIndex = root.parentNode ? root.parentNode.children.indexOf(root) : -1;
  const insertedAfterElement = rootIndex > 0 ? root.parentNode.children[rootIndex - 1] : null;
  const insertedAfter = scenario.targetId ? scenario.targetId : (insertedAfterElement && insertedAfterElement.id);
  if (scenario.expectedInsertedAfter) {
    assert(insertedAfter === scenario.expectedInsertedAfter, `${scenario.name}: AI search root was inserted after the wrong element.`);
  }
  trigger.focus();
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  const modal = document.body.querySelector(".hai-modal");
  const form = document.body.querySelector(".hai-search-form");

  assert(root.classList.contains("hai-root"), `${scenario.name}: AI search root was not inserted.`);
  assert(trigger && trigger.title === scenario.expectedTitle, `${scenario.name}: trigger tooltip mismatch.`);
  const cameraIconRendered = /<svg\b[^>]*class=["'][^"']*hai-camera-icon[^"']*["'][^>]*aria-hidden=["']true["'][^>]*focusable=["']false["']/i.test(trigger.innerHTML)
    && /<circle\b[^>]*cx=["']12["'][^>]*cy=["']13["'][^>]*r=["']3["']/i.test(trigger.innerHTML);
  const expectedAriaLabel = scenario.expectedAriaLabel || "AI 상품 검색";
  const triggerAriaLabel = trigger.getAttribute("aria-label");
  assert(cameraIconRendered, `${scenario.name}: trigger should render the camera icon.`);
  assert(triggerAriaLabel === expectedAriaLabel, `${scenario.name}: trigger accessible label mismatch.`);
  assert(modalInput.value === scenario.keyword, `${scenario.name}: existing search keyword was not copied.`);
  const initialPrefilledQuery = modalInput.value;
  assert(root.style["--hai-accent"] === scenario.expectedAccent, `${scenario.name}: accent color mismatch.`);
  assert(modal.style["--hai-z-index"] === String(scenario.expectedZIndex), `${scenario.name}: z-index mismatch.`);
  assert(modal.classList.contains("hai-open"), `${scenario.name}: trigger did not open the modal.`);
  assert(modal.getAttribute("aria-hidden") === "false", `${scenario.name}: open modal should be visible to assistive tech.`);
  assert(document.activeElement === modalInput, `${scenario.name}: modal did not focus the query input.`);
  const modalHtml = modal.innerHTML;
  const modalCopyComplete = [
    /해오름 판촉물 AI 검색/,
    /AI 상품 이미지 검색/,
    /찾고 싶은 판촉물/,
    /사진으로 비슷한 상품 찾기/,
    /AI가 비슷한 상품을 찾고 있습니다/,
  ].every((pattern) => pattern.test(modalHtml));
  const resultSectionHeadings = [
    /비슷한 카테고리 추천/,
    /상위 유사 상품 3개/,
    /관련 상품 리스트/,
  ].every((pattern) => pattern.test(modalHtml));
  assert(modalCopyComplete, `${scenario.name}: modal title, guidance, upload, or loading copy is incomplete.`);
  assert(resultSectionHeadings, `${scenario.name}: result section headings are incomplete.`);
  const closeButton = document.body.querySelector(".hai-icon-button");
  const backdrop = document.body.querySelector(".hai-backdrop");
  assert(closeButton && closeButton.getAttribute("aria-label") === "닫기", `${scenario.name}: close button is missing an accessible label.`);
  assert(backdrop && backdrop.dataset.close === "true", `${scenario.name}: backdrop should close the modal.`);
  modal.listeners.click({ target: closeButton });
  assert(!modal.classList.contains("hai-open"), `${scenario.name}: close button did not close the modal.`);
  assert(document.activeElement === trigger, `${scenario.name}: close button did not restore trigger focus.`);
  trigger.click();
  modal.listeners.click({ target: backdrop });
  assert(!modal.classList.contains("hai-open"), `${scenario.name}: backdrop did not close the modal.`);
  trigger.click();
  modal.listeners.keydown({ key: "Escape", preventDefault: () => {} });
  assert(!modal.classList.contains("hai-open"), `${scenario.name}: Escape did not close the modal.`);
  assert(modal.getAttribute("aria-hidden") === "true", `${scenario.name}: closed modal should be hidden from assistive tech.`);
  assert(document.activeElement === trigger, `${scenario.name}: closing modal did not restore trigger focus.`);
  const keyboardCloseRestoresFocus = document.activeElement === trigger;
  const modalCloseControls = true;
  page.input.value = scenario.updatedKeyword;
  trigger.click();
  assert(modal.classList.contains("hai-open"), `${scenario.name}: trigger did not reopen the modal after Escape.`);
  assert(modalInput.value === scenario.updatedKeyword, `${scenario.name}: reopened modal did not refresh the current search keyword.`);
  const refreshedQuery = modalInput.value;

  const fileInput = document.body.querySelector(".hai-file");
  const dropzone = document.body.querySelector(".hai-dropzone");
  const preview = document.body.querySelector(".hai-preview");
  const errorBox = document.body.querySelector(".hai-error");
  const supportedImageFormats = fileInput.getAttribute("accept") === "image/jpeg,image/png,image/webp" && /JPG, PNG, WEBP/.test(modalHtml);
  assert(supportedImageFormats, `${scenario.name}: upload control should restrict and describe JPG, PNG, WEBP images.`);
  let tabPrevented = false;
  dropzone.focus();
  modal.listeners.keydown({ key: "Tab", shiftKey: false, preventDefault: () => { tabPrevented = true; } });
  assert(tabPrevented && document.activeElement === closeButton, `${scenario.name}: Tab on the last modal control should cycle focus to the first control.`);
  let shiftTabPrevented = false;
  closeButton.focus();
  modal.listeners.keydown({ key: "Tab", shiftKey: true, preventDefault: () => { shiftTabPrevented = true; } });
  assert(shiftTabPrevented && document.activeElement === dropzone, `${scenario.name}: Shift+Tab on the first modal control should cycle focus to the last control.`);
  const keyboardTrapCyclesFocus = tabPrevented && shiftTabPrevented;
  modalInput.focus();
  const validFile = { type: "image/png", name: "valid.png", size: 1024 };
  const invalidFile = { type: "text/plain", name: "invalid.txt", size: 32 };
  const smallFile = { type: "image/png", name: "small.png", size: 512 };
  const damagedFile = { type: "image/png", name: "damaged.png", size: 512 };
  const maxImageMb = scenario.options.maxImageMb || 5;
  const oversizedFile = { type: "image/png", name: "too-large.png", size: maxImageMb * 1024 * 1024 + 1 };
  fileInput.files = [validFile];
  fileInput.listeners.change();
  finishImageLoad(preview, 32, 32);
  assert(preview.hidden === false, `${scenario.name}: valid image did not show preview.`);
  const previewImage = preview.querySelector("img");
  const removeButton = document.body.querySelector(".hai-remove");
  assert(previewImage && previewImage.alt === "업로드 이미지 미리보기", `${scenario.name}: preview image is missing accessible alt text.`);
  assert(removeButton && /이미지 삭제/.test(removeButton.textContent), `${scenario.name}: image remove button text mismatch.`);
  const validImagePreview = preview.hidden === false && previewImage.alt === "업로드 이미지 미리보기";
  fileInput.files = [invalidFile];
  fileInput.listeners.change();
  assert(errorBox.hidden === false && /JPG, PNG, WEBP/.test(errorBox.textContent), `${scenario.name}: invalid image type did not show an error.`);
  assert(preview.hidden === true, `${scenario.name}: invalid image type did not clear previous preview.`);
  fileInput.files = [oversizedFile];
  fileInput.listeners.change();
  assert(errorBox.hidden === false && /용량/.test(errorBox.textContent), `${scenario.name}: oversized image did not show an error.`);
  assert(preview.hidden === true, `${scenario.name}: oversized image did not clear previous preview.`);
  const oversizedImageRejected = /용량/.test(errorBox.textContent);
  fileInput.files = [smallFile];
  fileInput.listeners.change();
  finishImageLoad(preview, 8, 8);
  assert(errorBox.hidden === false && /최소/.test(errorBox.textContent), `${scenario.name}: small image did not show a minimum-size error.`);
  assert(preview.hidden === true, `${scenario.name}: small image did not clear previous preview.`);
  const smallImageRejected = /최소/.test(errorBox.textContent);
  fileInput.files = [damagedFile];
  fileInput.listeners.change();
  failImageLoad(preview);
  assert(errorBox.hidden === false && /읽을 수/.test(errorBox.textContent), `${scenario.name}: damaged image did not show a decode error.`);
  assert(preview.hidden === true, `${scenario.name}: damaged image did not clear previous preview.`);
  const damagedImageRejected = /읽을 수/.test(errorBox.textContent);

  const loading = document.body.querySelector(".hai-loading");
  const primaryButton = document.body.querySelector(".hai-primary");
  form.listeners.submit({ preventDefault: () => {} });
  form.listeners.submit({ preventDefault: () => {} });
  assert(loading.hidden === false, `${scenario.name}: loading message should be visible while search is pending.`);
  assert(primaryButton.disabled === true, `${scenario.name}: search button should be disabled while search is pending.`);
  assert(fetchCalls.length === 1, `${scenario.name}: duplicate submit while loading should not start another search.`);
  const duplicateSubmitBlocked = fetchCalls.length === 1;
  await new Promise((resolve) => setImmediate(resolve));
  assert(loading.hidden === true, `${scenario.name}: loading message should be hidden after search finishes.`);
  assert(primaryButton.disabled === false, `${scenario.name}: search button should be re-enabled after search finishes.`);
  const loadingState = true;

  assert(fetchCalls.length === 1, `${scenario.name}: search submit did not call fetch.`);
  const request = fetchCalls[0];
  const expectedApiBaseUrl = String(scenario.options.apiBaseUrl || "").replace(/\/+$/, "");
  assert(request.url === `${expectedApiBaseUrl}/api/ai-search`, `${scenario.name}: search URL mismatch.`);
  assert(request.options.method === "POST", `${scenario.name}: search method mismatch.`);
  assert(request.options.body.get("mall_id") === scenario.expectedMallId, `${scenario.name}: mall_id payload mismatch.`);
  assert(request.options.body.get("q") === scenario.updatedKeyword, `${scenario.name}: query payload mismatch.`);
  assert(!request.options.body.get("image"), `${scenario.name}: invalid image left a stale file in the search payload.`);
  if (scenario.options.apiKey) {
    assert(request.options.headers["X-API-Key"] === scenario.options.apiKey, `${scenario.name}: API key header mismatch.`);
  } else {
    assert(!request.options.headers["X-API-Key"], `${scenario.name}: API key header should be empty.`);
  }

  const score = document.body.querySelector(".hai-score");
  const productId = document.body.querySelector(".hai-product-id");
  const productTitle = document.body.querySelector("h4");
  const price = document.body.querySelector(".hai-price");
  const prices = Array.prototype.map.call(document.body.querySelectorAll(".hai-price"), (item) => item.textContent);
  const image = document.body.querySelector(".hai-image-link img");
  const imageLink = document.body.querySelector(".hai-image-link");
  assert(score && score.textContent === "유사도 91.2%", `${scenario.name}: score percent was not rendered clearly.`);
  assert(productId && productId.textContent === "상품번호 P001", `${scenario.name}: product id label was not rendered.`);
  assert(productTitle && productTitle.textContent === "검정 우산", `${scenario.name}: product name was not rendered.`);
  assert(price && /8,500원/.test(price.textContent), `${scenario.name}: product price was not rendered.`);
  assert(prices.some((text) => /견적문의상품/.test(text)), `${scenario.name}: quote price was not rendered.`);
  assert(image && image.alt === "검정 우산", `${scenario.name}: product image alt text should use product name.`);
  assert(imageLink && imageLink.getAttribute("aria-label") === "검정 우산", `${scenario.name}: product image link aria label mismatch.`);
  assert(imageLink.href === "https://shop.example.test/product/P001", `${scenario.name}: product image link URL mismatch.`);

  const categoryButton = document.body.querySelector(".hai-chip");
  assert(categoryButton, `${scenario.name}: category chip was not rendered.`);
  categoryButton.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === 2, `${scenario.name}: category click did not run a new search.`);
  assert(fetchCalls[1].options.body.get("category") === "우산", `${scenario.name}: category payload mismatch.`);
  assert(fetchCalls[1].options.body.get("offset") === "0", `${scenario.name}: category search should reset offset.`);

  const moreButton = document.body.querySelector(".hai-more");
  assert(moreButton && moreButton.parentNode.hidden === false, `${scenario.name}: more button was not shown.`);
  moreButton.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === 3, `${scenario.name}: more button did not request next page.`);
  assert(fetchCalls[2].options.body.get("category") === "우산", `${scenario.name}: more request lost category filter.`);
  assert(fetchCalls[2].options.body.get("offset") === "20", `${scenario.name}: more request offset mismatch.`);

  dropzone.listeners.dragenter({ preventDefault: () => {} });
  assert(dropzone.classList.contains("hai-drag"), `${scenario.name}: drag enter did not mark the upload dropzone.`);
  dropzone.listeners.drop({ preventDefault: () => {}, dataTransfer: { files: [validFile] } });
  finishImageLoad(preview, 32, 32);
  assert(!dropzone.classList.contains("hai-drag"), `${scenario.name}: drop did not clear the upload dropzone drag state.`);
  assert(preview.hidden === false, `${scenario.name}: dropped image did not restore preview after invalid file.`);
  form.listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === 4, `${scenario.name}: mixed image/text search did not call fetch.`);
  assert(fetchCalls[3].options.body.get("image") === validFile, `${scenario.name}: mixed search did not include the dropped image.`);

  const expectedClickUrl = `${expectedApiBaseUrl}/api/click-log`;

  function readSingleClickLog(startFetchCount, startBeaconCount, label) {
    if (scenario.options.apiKey) {
      const clickLogs = fetchCalls.slice(startFetchCount).filter((call) => call.url === expectedClickUrl);
      assert(clickLogs.length === 1, `${scenario.name}: ${label} click should send exactly one click-log request.`);
      const clickLog = clickLogs[0];
      assert(clickLog.options.method === "POST", `${scenario.name}: ${label} click log method mismatch.`);
      assert(clickLog.options.headers["X-API-Key"] === scenario.options.apiKey, `${scenario.name}: ${label} click log API key header mismatch.`);
      assert(!/[?&]api_key=/.test(clickLog.url), `${scenario.name}: ${label} click log API key should not be sent in the URL.`);
      assert(beaconCalls.length === startBeaconCount, `${scenario.name}: ${label} click with API key should use fetch headers, not sendBeacon.`);
      return { payload: JSON.parse(clickLog.options.body), transport: "fetch" };
    }
    const beacons = beaconCalls.slice(startBeaconCount);
    assert(beacons.length === 1, `${scenario.name}: ${label} click should send exactly one beacon click-log request.`);
    const beacon = beacons[0];
    assert(beacon && beacon.url === expectedClickUrl, `${scenario.name}: ${label} click log beacon URL mismatch.`);
    assert(!/[?&]api_key=/.test(beacon.url), `${scenario.name}: ${label} click log beacon URL should not include api_key.`);
    return { payload: JSON.parse(beacon.body), transport: "sendBeacon" };
  }

  const beforeImageClickFetchCount = fetchCalls.length;
  const beforeImageClickBeaconCount = beaconCalls.length;
  imageLink.click();
  await new Promise((resolve) => setImmediate(resolve));
  const imageClick = readSingleClickLog(beforeImageClickFetchCount, beforeImageClickBeaconCount, "image link");
  assert(imageClick.payload.mall_id === scenario.expectedMallId, `${scenario.name}: image link click log mall_id mismatch.`);
  assert(imageClick.payload.product_id === "P001", `${scenario.name}: image link click log product_id mismatch.`);
  assert(imageClick.payload.product_url === "https://shop.example.test/product/P001", `${scenario.name}: image link click log product_url mismatch.`);
  assert(imageClick.payload.position === 1, `${scenario.name}: image link click log position mismatch.`);
  assert(imageClick.payload.query_type === "text_image", `${scenario.name}: image link click log query_type should preserve the rendered search type.`);

  const detail = document.body.querySelector(".hai-detail");
  assert(detail, `${scenario.name}: detail link was not rendered.`);
  assert(detail.href === "https://shop.example.test/product/P001", `${scenario.name}: detail link URL mismatch.`);
  const beforeDetailClickFetchCount = fetchCalls.length;
  const beforeDetailClickBeaconCount = beaconCalls.length;
  detail.click();
  await new Promise((resolve) => setImmediate(resolve));
  const detailClick = readSingleClickLog(beforeDetailClickFetchCount, beforeDetailClickBeaconCount, "detail link");
  const clickPayload = detailClick.payload;
  const clickLogTransport = detailClick.transport;
  assert(clickPayload.mall_id === scenario.expectedMallId, `${scenario.name}: click log mall_id mismatch.`);
  assert(clickPayload.product_id === "P001", `${scenario.name}: click log product_id mismatch.`);
  assert(clickPayload.product_url === "https://shop.example.test/product/P001", `${scenario.name}: click log product_url mismatch.`);
  assert(clickPayload.position === 1, `${scenario.name}: click log position mismatch.`);
  assert(clickPayload.query_type === "text_image", `${scenario.name}: click log query_type should preserve the rendered search type.`);
  const imageAndDetailClickLogging = imageClick.payload.product_id === "P001" && clickPayload.product_id === "P001";

  removeButton.click();
  assert(preview.hidden === true, `${scenario.name}: image remove button did not hide preview.`);
  const removeSearchStart = fetchCalls.length;
  form.listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === removeSearchStart + 1, `${scenario.name}: search after image removal did not call fetch.`);
  assert(!fetchCalls[removeSearchStart].options.body.get("image"), `${scenario.name}: image remove button left a stale image in the next search payload.`);
  const imageRemoveClearsPayload = preview.hidden === true && !fetchCalls[removeSearchStart].options.body.get("image");

  const beforeNoResultFetchCount = fetchCalls.length;
  modalInput.value = "없는 상품";
  searchSuccessResponse = {
    top: [],
    items: [],
    suggested_categories: ["타올"],
    meta: {
      query_type: "text",
      offset: 0,
      has_more: false,
      next_offset: null,
      low_confidence: true,
      notice: "검색 결과가 없습니다. 다른 검색어를 추가하거나 더 선명한 이미지를 사용해 주세요.",
    },
  };
  form.listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === beforeNoResultFetchCount + 1, `${scenario.name}: no-result search did not call fetch.`);
  const noResultNotice = document.body.querySelector(".hai-notice");
  const noResultEmpty = document.body.querySelector(".hai-empty");
  assert(noResultNotice.hidden === false && /검색 결과가 없습니다/.test(noResultNotice.textContent), `${scenario.name}: no-result API notice was not shown.`);
  assert(noResultEmpty.hidden === true, `${scenario.name}: no-result API notice should suppress the generic empty state.`);
  searchSuccessResponse = null;
  const noResultNoticeSuppressesGenericEmpty = noResultNotice.hidden === false && noResultEmpty.hidden === true;

  const beforeErrorFetchCount = fetchCalls.length;
  modalInput.value = "요청 과다";
  searchErrorResponse = { status: 429, body: { detail: "rate limit" } };
  form.listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === beforeErrorFetchCount + 1, `${scenario.name}: failed search did not call fetch.`);
  assert(errorBox.hidden === false && /요청이 많/.test(errorBox.textContent), `${scenario.name}: rate limit response did not show a friendly error.`);
  assert(document.body.querySelector(".hai-top").hidden === true, `${scenario.name}: failed new search should hide stale top results.`);
  assert(document.body.querySelector(".hai-top-list").children.length === 0, `${scenario.name}: failed new search should clear stale top result cards.`);
  assert(document.body.querySelector(".hai-item-list").children.length === 0, `${scenario.name}: failed new search should clear stale related result cards.`);
  assert(document.body.querySelector(".hai-more-wrap").hidden === true, `${scenario.name}: failed new search should hide stale pagination.`);
  searchErrorResponse = null;
  const rateLimitErrorClearsStaleResults = /요청이 많/.test(errorBox.textContent);

  return {
    name: scenario.name,
    mallId: request.options.body.get("mall_id"),
    insertedAfter,
    attachMode: root.getAttribute("data-hai-attach-mode"),
    prefilledQuery: initialPrefilledQuery,
    refreshedQuery,
    triggerTitle: trigger.title,
    triggerAriaLabel,
    cameraIconRendered,
    modalCopyComplete,
    supportedImageFormats,
    resultSectionHeadings,
    accentColor: root.style["--hai-accent"],
    categoryRefetch: fetchCalls[1].options.body.get("category"),
    moreOffset: fetchCalls[2].options.body.get("offset"),
    mixedQueryType: fetchCalls[3].options.body.get("image") ? "text_image" : "text",
    clickLogTransport,
    clickLogQueryType: clickPayload.query_type,
    clickLogProductId: clickPayload.product_id,
    imageAndDetailClickLogging,
    oversizedImageRejected,
    smallImageRejected,
    damagedImageRejected,
    validImagePreview,
    imageRemoveClearsPayload,
    dragDropUpload: fetchCalls[3].options.body.get("image") === validFile,
    responsiveLayout: true,
    keyboardCloseRestoresFocus,
    keyboardTrapCyclesFocus,
    loadingState,
    duplicateSubmitBlocked,
    modalCloseControls,
    rateLimitErrorClearsStaleResults,
    noResultNoticeSuppressesGenericEmpty,
    resultFieldsRendered: true,
  };
}

function assertConflictingAliasRejected() {
  const document = new FakeDocument();
  const context = {
    document,
    window: {},
    console,
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  let message = "";
  try {
    context.window.HaeorumAISearch.init({
      target: "#haeorum-ai-search",
      mallId: "shop001",
      siteId: "partner002",
      apiBaseUrl: "http://localhost:8000",
    });
  } catch (error) {
    message = error.message || String(error);
  }
  assert(/mallId and siteId must match/.test(message), "conflicting mallId/siteId options should be rejected before mounting.");
  return { conflictingMallSiteIdRejected: true };
}

function assertMallIdValidation() {
  const invalidCases = [
    { mallId: "shop001/evil" },
    { mallId: "shop001?evil" },
    { mallId: "shop_001" },
    { mallId: "-shop001" },
    { mallId: "shop001-" },
    { siteId: "partner002/evil" },
    { mallId: "shop001", siteId: "shop001/evil" },
  ];
  invalidCases.forEach((identifiers) => {
    const document = new FakeDocument();
    buildSearchPage(document, {
      inputId: "mall-id-keyword",
      submitId: "mall-id-submit",
      targetId: "mall-id-ai-search",
      keyword: "검은 우산",
    });
    const context = {
      document,
      window: {},
      console,
    };
    context.window.window = context.window;
    const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
    vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
    let message = "";
    try {
      context.window.HaeorumAISearch.init(Object.assign({
        target: "#mall-id-ai-search",
        apiBaseUrl: "http://localhost:8000",
      }, identifiers));
    } catch (error) {
      message = error.message || String(error);
    }
    assert(/must contain only letters, numbers, and hyphens/.test(message), `unsafe mallId/siteId should be rejected: ${JSON.stringify(identifiers)}`);
  });

  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "blank-mall-id-keyword",
    submitId: "blank-mall-id-submit",
    targetId: "blank-mall-id-ai-search",
    keyword: "검은 우산",
  });
  const context = {
    document,
    window: {},
    console,
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  let message = "";
  try {
    context.window.HaeorumAISearch.init({
      target: "#blank-mall-id-ai-search",
      mallId: "",
      siteId: "",
      apiBaseUrl: "http://localhost:8000",
    });
  } catch (error) {
    message = error.message || String(error);
  }
  assert(/mallId or siteId is required/.test(message), "blank mallId/siteId should be rejected before mounting.");
  return { unsafeMallIdRejected: true };
}

function assertApiBaseUrlValidation() {
  const invalidValues = [
    "",
    "/api",
    "javascript:alert(1)",
    "https://user:pass@api.example.test",
    "https://api.example.test?token=secret",
    "https://api.example.test#fragment",
    "https://api.example.test\\bad",
    "http://api.example.test:99999",
  ];
  invalidValues.forEach((apiBaseUrl) => {
    const document = new FakeDocument();
    buildSearchPage(document, {
      inputId: "api-keyword",
      submitId: "api-submit",
      targetId: "api-ai-search",
      keyword: "검은 우산",
    });
    const context = {
      document,
      window: {},
      console,
    };
    context.window.window = context.window;
    const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
    vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
    let message = "";
    try {
      context.window.HaeorumAISearch.init({
        target: "#api-ai-search",
        mallId: "shop001",
        apiBaseUrl,
      });
    } catch (error) {
      message = error.message || String(error);
    }
    assert(/apiBaseUrl must be an absolute HTTP\(S\) URL/.test(message), `unsafe apiBaseUrl should be rejected: ${apiBaseUrl}`);
  });
  return { unsafeApiBaseUrlRejected: true };
}

async function assertScriptSrcApiBaseUrlFallback() {
  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "script-src-keyword",
    submitId: "script-src-submit",
    targetId: "script-src-ai-search",
    keyword: "검은 우산",
  });
  const script = document.createElement("script");
  script.src = "https://ai.example.test/widget.js?v=20260524";
  document.currentScript = script;
  document.head.appendChild(script);
  const fetchCalls = [];
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      return {
        ok: true,
        json: async () => ({
          top: [],
          items: [],
          suggested_categories: [],
          meta: { query_type: "text", offset: 0, has_more: false, next_offset: null },
        }),
      };
    },
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#script-src-ai-search",
    mallId: "shop001",
  });
  const trigger = document.body.querySelector(".hai-trigger");
  assert(trigger, "script src API base fallback should mount the widget.");
  trigger.click();
  document.body.querySelector(".hai-query").value = "검은 우산";
  document.body.querySelector(".hai-search-form").listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === 1, "script src API base fallback should submit a search request.");
  assert(fetchCalls[0].url === "https://ai.example.test/api/ai-search", "script src API base fallback should derive the API origin.");
  return { scriptSrcApiBaseUrlFallback: true };
}

async function assertScriptDataAttributeAutoInit() {
  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "data-keyword",
    submitId: "data-submit",
    targetId: "",
    keyword: "검은 우산",
  });
  const script = document.createElement("script");
  script.src = "https://ai.example.test/widget.js";
  script.setAttribute("data-hai-auto-init", "true");
  script.setAttribute("data-attach-to-search-input", "#data-keyword");
  script.setAttribute("data-attach-after-selector", "#data-submit");
  script.setAttribute("data-mall-id", "shop001");
  script.setAttribute("data-api-key", "public-shop001");
  script.setAttribute("data-trigger-title", "AI자동검색");
  script.setAttribute("data-accent-color", "#005bac");
  document.currentScript = script;
  document.head.appendChild(script);
  const fetchCalls = [];
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    fetch: async (url, options) => {
      fetchCalls.push({ url, options });
      return {
        ok: true,
        json: async () => ({
          top: [],
          items: [],
          suggested_categories: [],
          meta: { query_type: "text", offset: 0, has_more: false, next_offset: null },
        }),
      };
    },
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  const root = document.body.querySelector(".hai-root");
  const trigger = document.body.querySelector(".hai-trigger");
  assert(root, "script data attribute auto init should mount the widget.");
  assert(root.style["--hai-accent"] === "#005bac", "script data attribute auto init should apply design options.");
  assert(trigger && trigger.title === "AI자동검색", "script data attribute auto init should apply trigger title.");
  trigger.click();
  document.body.querySelector(".hai-search-form").listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert(fetchCalls.length === 1, "script data attribute auto init should submit a search request.");
  assert(fetchCalls[0].url === "https://ai.example.test/api/ai-search", "script data attribute auto init should derive the API origin.");
  assert(fetchCalls[0].options.headers["X-API-Key"] === "public-shop001", "script data attribute auto init should pass the API key as a header.");
  assert(fetchCalls[0].options.body.get("mall_id") === "shop001", "script data attribute auto init should pass the mall id.");
  assert(fetchCalls[0].options.body.get("q") === "검은 우산", "script data attribute auto init should prefill the existing search query.");
  return { scriptDataAttributeAutoInit: true };
}

async function assertUnsafeProductUrlsNeutralized() {
  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "unsafe-keyword",
    submitId: "unsafe-submit",
    targetId: "unsafe-ai-search",
    keyword: "악성 링크",
  });
  const beaconCalls = [];
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    Blob: class {
      constructor(parts, options) {
        this.parts = parts || [];
        this.type = options && options.type;
      }
    },
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: {
      sendBeacon: (url, blob) => {
        beaconCalls.push({ url, body: (blob.parts || []).join("") });
        return true;
      },
    },
    fetch: async (url) => {
      if (String(url).endsWith("/api/click-log")) {
        return { ok: true, json: async () => ({ ok: true }) };
      }
      return {
        ok: true,
        json: async () => ({
          top: [
            {
              product_id: "P-XSS",
              name: "악성 링크 상품",
              category: "보안",
              price: 1,
              image_url: "javascript:alert(2)",
              product_url: "javascript:alert(1)",
              score_percent: 50,
            },
          ],
          items: [
            {
              product_id: "P-CRED",
              name: "사용자 정보 URL 상품",
              category: "보안",
              price: 2,
              image_url: "https://token@cdn.example.test/image.png",
              product_url: "https://user:pass@shop.example.test/product/P-CRED",
              score_percent: 49,
            },
            {
              product_id: "P-REL",
              name: "상대 URL 상품",
              category: "보안",
              price: 3,
              image_url: "/images/P-REL.png",
              product_url: "/product/P-REL",
              score_percent: 48,
            },
            {
              product_id: "P-DATA",
              name: "Data URL 이미지 상품",
              category: "보안",
              price: 4,
              image_url: "data:image/png;base64,AAAA",
              product_url: "/product/P-DATA",
              score_percent: 47,
            },
            {
              product_id: "P-LOCAL",
              name: "로컬 URL 상품",
              category: "보안",
              price: 5,
              image_url: "https://localhost./image.png",
              product_url: "https://shop.localhost./product/P-LOCAL",
              score_percent: 46,
            },
            {
              product_id: "P-LINK",
              name: "Link-local URL 상품",
              category: "보안",
              price: 6,
              image_url: "http://169.254.169.254/latest/meta-data",
              product_url: "http://[fe80::1]/product/P-LINK",
              score_percent: 45,
            },
          ],
          suggested_categories: [],
          meta: { query_type: "text", offset: 0, has_more: false, next_offset: null },
        }),
      };
    },
  };
  context.window.window = context.window;

  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#unsafe-ai-search",
    attachToSearchInput: "#unsafe-keyword",
    mallId: "shop001",
    apiBaseUrl: "http://localhost:8000",
  });

  document.body.querySelector(".hai-trigger").click();
  document.body.querySelector(".hai-search-form").listeners.submit({ preventDefault: () => {} });
  await new Promise((resolve) => setImmediate(resolve));

  const imageLinks = document.body.querySelectorAll(".hai-image-link");
  const detailLinks = document.body.querySelectorAll(".hai-detail");
  const images = document.body.querySelectorAll(".hai-image-link img");
  assert(imageLinks.length === 6, "unsafe URL check should render all unsafe products.");
  imageLinks.forEach((imageLink) => {
    assert(imageLink.href === "#", "unsafe product_url should be neutralized on the image link.");
  });
  detailLinks.forEach((detailLink) => {
    assert(detailLink.href === "#", "unsafe product_url should be neutralized on the detail link.");
  });
  images.forEach((image) => {
    assert(image.src === "", "unsafe product image_url should not be rendered as an image src.");
  });

  imageLinks.forEach((imageLink) => imageLink.click());
  await new Promise((resolve) => setImmediate(resolve));
  assert(beaconCalls.length === 6, "neutralized unsafe product links should still record clicks.");
  const payloads = beaconCalls.map((call) => JSON.parse(call.body));
  assert(payloads.some((payload) => payload.product_id === "P-XSS"), "neutralized javascript URL click should keep product_id.");
  assert(payloads.some((payload) => payload.product_id === "P-CRED"), "neutralized credential URL click should keep product_id.");
  assert(payloads.some((payload) => payload.product_id === "P-REL"), "neutralized relative URL click should keep product_id.");
  assert(payloads.some((payload) => payload.product_id === "P-DATA"), "neutralized data image URL click should keep product_id.");
  assert(payloads.some((payload) => payload.product_id === "P-LOCAL"), "neutralized localhost URL click should keep product_id.");
  assert(payloads.some((payload) => payload.product_id === "P-LINK"), "neutralized link-local URL click should keep product_id.");
  payloads.forEach((payload) => {
    assert(payload.product_url === null, "unsafe product_url should not be sent to click logs.");
  });
  return { unsafeProductUrlsNeutralized: true };
}

function assertDeferredInitUntilDomReady() {
  const document = new FakeDocument();
  document.readyState = "loading";
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#late-ai-search",
    attachToSearchInput: "#late-keyword",
    attachAfterSelector: "#late-submit",
    mallId: "shop-late",
    apiBaseUrl: "http://localhost:8000",
  });
  assert(!document.body.querySelector(".hai-root"), "early init should wait instead of mounting before DOM exists.");
  buildSearchPage(document, {
    inputId: "late-keyword",
    submitId: "late-submit",
    targetId: "late-ai-search",
    keyword: "늦게 로드된 검색어",
  });
  document.readyState = "interactive";
  document.dispatchEvent({ type: "DOMContentLoaded" });
  const root = document.getElementById("late-ai-search");
  const trigger = root && root.querySelector(".hai-trigger");
  assert(root && root.classList.contains("hai-root"), "deferred init should mount once DOMContentLoaded fires.");
  assert(trigger && trigger.getAttribute("aria-label") === "AI 상품 검색", "deferred init should build the widget trigger.");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(modalInput && modalInput.value === "늦게 로드된 검색어", "deferred init should still prefill from the late search input.");
  return { deferredInitUntilDomReady: true };
}

function assertRepeatedInitReplacesWidget() {
  FakeMutationObserver.reset();
  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "duplicate-keyword",
    submitId: "duplicate-submit",
    keyword: "중복 초기화 검색어",
  });
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  const options = {
    target: "",
    attachToSearchInput: "#duplicate-keyword",
    attachAfterSelector: "#duplicate-submit",
    mallId: "shop-duplicate",
    apiBaseUrl: "http://localhost:8000",
  };
  context.window.HaeorumAISearch.init(Object.assign({}, options, { triggerTitle: "첫 AI검색" }));
  context.window.HaeorumAISearch.init(Object.assign({}, options, { triggerTitle: "두 번째 AI검색" }));
  const roots = document.body.querySelectorAll(".hai-root");
  const modals = document.body.querySelectorAll(".hai-modal");
  const trigger = document.body.querySelector(".hai-trigger");
  assert(roots.length === 1, "repeated init should leave exactly one widget root.");
  assert(modals.length === 1, "repeated init should leave exactly one modal.");
  assert(trigger && trigger.title === "두 번째 AI검색", "repeated init should keep the latest widget options.");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(modalInput && modalInput.value === "중복 초기화 검색어", "repeated init should keep prefill behavior.");
  context.window.HaeorumAISearch.destroy();
  assert(document.body.querySelectorAll(".hai-root").length === 0, "destroy should remove the attached widget root.");
  assert(document.body.querySelectorAll(".hai-modal").length === 0, "destroy should remove the widget modal.");
  return { repeatedInitReplacesWidget: true };
}

function assertCssSpecialIdSelectorFallback() {
  FakeMutationObserver.reset();
  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "ctl00:keyword",
    submitId: "ctl00:submit",
    targetId: "ctl00:ai-search",
    keyword: "콜론 ID 검색어",
  });
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#ctl00:ai-search",
    attachToSearchInput: "#ctl00:keyword",
    attachAfterSelector: "#ctl00:submit",
    mallId: "shop-legacy",
    apiBaseUrl: "http://localhost:8000",
  });
  const root = document.getElementById("ctl00:ai-search");
  const trigger = root && root.querySelector(".hai-trigger");
  assert(root && root.classList.contains("hai-root"), "special-character ID selector fallback should mount on the target.");
  assert(trigger && trigger.getAttribute("aria-label") === "AI 상품 검색", "special-character ID selector fallback should build the widget trigger.");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(modalInput && modalInput.value === "콜론 ID 검색어", "special-character ID selector fallback should prefill from the search input.");
  return { cssSpecialIdSelectorFallback: true };
}

function assertComplexCssSpecialIdSelectorFallback() {
  FakeMutationObserver.reset();
  const document = new FakeDocument();
  buildSearchPage(document, {
    formId: "legacyForm",
    inputId: "ctl00:complex-keyword",
    submitId: "ctl00:complex-submit",
    targetId: "ctl00:complex-ai-search",
    keyword: "복합 selector 검색어",
  });
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#legacyForm #ctl00:complex-ai-search",
    attachToSearchInput: "#legacyForm #ctl00:complex-keyword",
    attachAfterSelector: "#legacyForm #ctl00:complex-submit",
    mallId: "shop-legacy",
    apiBaseUrl: "http://localhost:8000",
  });
  const root = document.getElementById("ctl00:complex-ai-search");
  const trigger = root && root.querySelector(".hai-trigger");
  assert(root && root.classList.contains("hai-root"), "complex special-character ID selector fallback should mount on the target.");
  assert(trigger && trigger.getAttribute("aria-label") === "AI 상품 검색", "complex special-character ID selector fallback should build the widget trigger.");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(modalInput && modalInput.value === "복합 selector 검색어", "complex special-character ID selector fallback should prefill from the search input.");
  return { complexCssSpecialIdSelectorFallback: true };
}

function assertAmbiguousExplicitSelectorRejected() {
  FakeMutationObserver.reset();
  const document = new FakeDocument();
  buildSearchPage(document, {
    inputId: "ambiguous-keyword-a",
    inputClass: "shared-search",
    submitId: "ambiguous-submit-a",
    targetId: "ambiguous-ai-search",
    keyword: "첫 검색어",
  });
  const secondInput = document.createElement("input");
  secondInput.id = "ambiguous-keyword-b";
  secondInput.className = "shared-search";
  secondInput.value = "두 번째 검색어";
  document.body.appendChild(secondInput);
  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  let message = "";
  try {
    context.window.HaeorumAISearch.init({
      target: "#ambiguous-ai-search",
      attachToSearchInput: ".shared-search",
      mallId: "shop001",
      apiBaseUrl: "http://localhost:8000",
    });
  } catch (error) {
    message = error.message || String(error);
  }
  assert(/selector matched multiple elements/.test(message), "ambiguous explicit selectors should be rejected before mounting.");
  assert(!document.body.querySelector(".hai-root"), "ambiguous explicit selectors should not mount the widget.");
  return { ambiguousExplicitSelectorRejected: true };
}

function assertDynamicAutoAttachAfterDomMutation() {
  FakeMutationObserver.reset();
  const timers = [];
  const document = new FakeDocument();
  document.readyState = "complete";
  const context = {
    document,
    window: {
      setTimeout: (callback, delay) => {
        const timer = { callback, delay, cleared: false };
        timers.push(timer);
        return timer;
      },
      clearTimeout: (timer) => {
        if (timer) {
          timer.cleared = true;
        }
      },
      MutationObserver: FakeMutationObserver,
    },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    mallId: "shop-dynamic",
    apiBaseUrl: "http://localhost:8000",
    mountWaitMs: 1500,
  });
  assert(!document.body.querySelector(".hai-root"), "dynamic auto attach should wait before the search form exists.");
  assert(FakeMutationObserver.observers.length === 1, "dynamic auto attach should observe DOM mutations.");
  buildSearchPage(document, {
    inputId: "dynamic-keyword",
    inputName: "keyword",
    inputType: "search",
    inputPlaceholder: "상품명 검색",
    submitId: "dynamic-submit",
    submitText: "검색",
    keyword: "동적 검색어",
  });
  const root = document.body.querySelector(".hai-root");
  const trigger = document.body.querySelector(".hai-trigger");
  const rootIndex = root && root.parentNode ? root.parentNode.children.indexOf(root) : -1;
  const insertedAfterElement = rootIndex > 0 ? root.parentNode.children[rootIndex - 1] : null;
  assert(root && root.getAttribute("data-hai-attach-mode") === "auto", "dynamic auto attach should mount in auto mode.");
  assert(insertedAfterElement && insertedAfterElement.id === "dynamic-submit", "dynamic auto attach should insert after the detected submit button.");
  assert(FakeMutationObserver.observers.length === 0, "dynamic auto attach should stop observing after mount.");
  assert(timers.some((timer) => timer.cleared), "dynamic auto attach should clear its timeout after mount.");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(modalInput && modalInput.value === "동적 검색어", "dynamic auto attach should prefill from the late search input.");
  return { dynamicAutoAttachAfterDomMutation: true };
}

function assertAutoAttachSkipsHiddenAndDisabledSearchInputs() {
  const document = new FakeDocument();
  const hiddenForm = document.createElement("form");
  hiddenForm.id = "hidden-search-form";
  hiddenForm.setAttribute("style", "display: none");
  const hiddenInput = document.createElement("input");
  hiddenInput.id = "hidden-keyword";
  hiddenInput.type = "search";
  hiddenInput.value = "숨은 검색어";
  hiddenForm.appendChild(hiddenInput);
  const hiddenSubmit = document.createElement("button");
  hiddenSubmit.id = "hidden-submit";
  hiddenSubmit.type = "submit";
  hiddenSubmit.textContent = "검색";
  hiddenForm.appendChild(hiddenSubmit);
  document.body.appendChild(hiddenForm);

  const disabledForm = document.createElement("form");
  disabledForm.id = "disabled-search-form";
  const disabledInput = document.createElement("input");
  disabledInput.id = "disabled-keyword";
  disabledInput.type = "search";
  disabledInput.value = "비활성 검색어";
  disabledInput.disabled = true;
  disabledForm.appendChild(disabledInput);
  const disabledSubmit = document.createElement("button");
  disabledSubmit.id = "disabled-submit";
  disabledSubmit.type = "submit";
  disabledSubmit.textContent = "검색";
  disabledForm.appendChild(disabledSubmit);
  document.body.appendChild(disabledForm);

  const visibleForm = document.createElement("form");
  visibleForm.id = "visible-search-form";
  const visibleInput = document.createElement("input");
  visibleInput.id = "visible-keyword";
  visibleInput.type = "search";
  visibleInput.value = "보이는 검색어";
  visibleForm.appendChild(visibleInput);
  const visibleSubmit = document.createElement("button");
  visibleSubmit.id = "visible-submit";
  visibleSubmit.type = "submit";
  visibleSubmit.textContent = "검색";
  visibleForm.appendChild(visibleSubmit);
  document.body.appendChild(visibleForm);

  const context = {
    document,
    window: { setTimeout: (callback) => callback() },
    console,
    FormData: FakeFormData,
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    mallId: "shop001",
    apiBaseUrl: "http://localhost:8000",
  });

  const root = document.body.querySelector(".hai-root");
  const rootIndex = root && root.parentNode ? root.parentNode.children.indexOf(root) : -1;
  const insertedAfterElement = rootIndex > 0 ? root.parentNode.children[rootIndex - 1] : null;
  const trigger = document.body.querySelector(".hai-trigger");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(root && root.getAttribute("data-hai-attach-mode") === "auto", "auto attach should still mount when visible search input exists.");
  assert(insertedAfterElement && insertedAfterElement.id === "visible-submit", "auto attach should skip hidden/disabled search controls.");
  assert(modalInput && modalInput.value === "보이는 검색어", "auto attach should prefill from the visible search input.");
  return { autoAttachSkipsHiddenDisabledSearchInputs: true };
}

function assertFallbackFloatingMountsWithoutSearchForm() {
  const document = new FakeDocument();
  const context = {
    document,
    window: { setTimeout: (callback) => callback(), clearTimeout: () => {} },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "",
    mallId: "shop-floating",
    apiBaseUrl: "http://localhost:8000",
    fallbackFloating: true,
    mountWaitMs: 0,
  });

  const root = document.body.querySelector(".hai-root");
  const trigger = document.body.querySelector(".hai-trigger");
  const style = document.getElementById("haeorum-ai-search-style");
  assert(root && root.getAttribute("data-hai-attach-mode") === "floating", "fallback floating should mount without a legacy search form.");
  assert(root.parentNode === document.body, "fallback floating root should be appended to body.");
  assert(style && /data-hai-attach-mode="floating"/.test(style.textContent), "fallback floating should have fixed-position CSS.");
  trigger.click();
  const modal = document.body.querySelector(".hai-modal");
  const modalInput = document.body.querySelector(".hai-query");
  assert(modal && modal.classList.contains("hai-open"), "fallback floating trigger should open the modal.");
  assert(modalInput && modalInput.value === "", "fallback floating should not prefill a missing legacy search input.");
  return { fallbackFloatingMountsWithoutSearchForm: true };
}

function assertFallbackFloatingCoversMissingExplicitTargetWithoutWait() {
  const document = new FakeDocument();
  const context = {
    document,
    window: { setTimeout: (callback) => callback(), clearTimeout: () => {} },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#missing-ai-search",
    mallId: "shop-floating",
    apiBaseUrl: "http://localhost:8000",
    fallbackFloating: true,
    mountWaitMs: 0,
  });

  const root = document.body.querySelector(".hai-root");
  const trigger = document.body.querySelector(".hai-trigger");
  assert(root && root.getAttribute("data-hai-attach-mode") === "floating", "fallback floating should cover a missing explicit target without waiting.");
  trigger.click();
  const modalInput = document.body.querySelector(".hai-query");
  assert(modalInput && modalInput.value === "", "missing explicit target fallback should not require a legacy search input.");
  return { fallbackFloatingCoversMissingExplicitTargetWithoutWait: true };
}

function assertFallbackFloatingCoversMissingExplicitTargetAfterWait() {
  FakeMutationObserver.reset();
  const timers = [];
  const document = new FakeDocument();
  document.readyState = "complete";
  const context = {
    document,
    window: {
      setTimeout: (callback, delay) => {
        const timer = { callback, delay, cleared: false };
        timers.push(timer);
        return timer;
      },
      clearTimeout: (timer) => {
        if (timer) {
          timer.cleared = true;
        }
      },
      MutationObserver: FakeMutationObserver,
    },
    console,
    FormData: FakeFormData,
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    navigator: { sendBeacon: () => true },
    fetch: async () => ({ ok: true, json: async () => ({ top: [], items: [], suggested_categories: [], meta: { query_type: "text", offset: 0, has_more: false, next_offset: null } }) }),
  };
  context.window.window = context.window;
  const widgetPath = path.join(__dirname, "..", "widget", "widget.js");
  vm.runInNewContext(fs.readFileSync(widgetPath, "utf-8"), context, { filename: widgetPath });
  context.window.HaeorumAISearch.init({
    target: "#missing-ai-search",
    mallId: "shop-floating",
    apiBaseUrl: "http://localhost:8000",
    fallbackFloating: true,
    mountWaitMs: 1200,
  });
  assert(!document.body.querySelector(".hai-root"), "missing explicit target should wait for a late mount before falling back.");
  assert(FakeMutationObserver.observers.length === 1, "missing explicit target fallback should observe late DOM mutations.");
  assert(timers.length === 1 && timers[0].delay === 1200, "missing explicit target fallback should use the configured wait.");

  timers[0].callback();

  const root = document.body.querySelector(".hai-root");
  assert(root && root.getAttribute("data-hai-attach-mode") === "floating", "missing explicit target should fall back to floating after mountWaitMs.");
  assert(FakeMutationObserver.observers.length === 0, "missing explicit target fallback should stop observing after timeout fallback.");
  assert(timers[0].cleared, "missing explicit target fallback should clear the pending wait timer.");
  return { fallbackFloatingCoversMissingExplicitTargetAfterWait: true };
}

function parseArgs(argv) {
  const options = { output: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--output") {
      index += 1;
      if (!argv[index]) {
        throw new Error("--output requires a path.");
      }
      options.output = argv[index];
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }
  return options;
}

function writeReport(report, outputPath) {
  const text = JSON.stringify(report, null, 2);
  if (outputPath) {
    fs.mkdirSync(path.dirname(path.resolve(outputPath)), { recursive: true });
    fs.writeFileSync(outputPath, `${text}\n`, "utf8");
  }
  console.log(text);
}

async function main(argv = process.argv.slice(2)) {
  const cli = parseArgs(argv);
  if (cli.help) {
    console.log("Usage: node scripts/widget_dom_check.js [--output <widget-dom.json>]");
    return;
  }
  const scenarios = [
    {
      name: "shop001-target-container",
      inputId: "keyword",
      submitId: "search-submit",
      targetId: "haeorum-ai-search",
      keyword: "검은 우산",
      updatedKeyword: "고급 볼펜",
      expectedTitle: "AI검색",
      expectedAccent: "#0f766e",
      expectedZIndex: 2147483000,
      expectedMallId: "shop001",
      options: {
        target: "#haeorum-ai-search",
        attachToSearchInput: "#keyword",
        mallId: "shop001",
        apiKey: "public-shop001-dev-key",
        apiBaseUrl: "http://localhost:8000",
        triggerTitle: "AI검색",
        accentColor: "#0f766e",
        accentSoftColor: "#ecfdf5",
        zIndex: 2147483000,
      },
    },
    {
      name: "partner002-site-id-alias",
      inputId: "partner-keyword",
      submitId: "partner-submit",
      targetId: "",
      keyword: "스텐 텀블러",
      updatedKeyword: "보온 텀블러",
      expectedTitle: "AI이미지검색",
      expectedAccent: "#005bac",
      expectedZIndex: 2147483100,
      expectedMallId: "partner002",
      options: {
        target: "",
        attachToSearchInput: "#partner-keyword",
        attachAfterSelector: "#partner-submit",
        siteId: "partner002",
        apiKey: "public-partner002-key",
        apiBaseUrl: "http://localhost:8000",
        triggerTitle: "AI이미지검색",
        accentColor: "#005bac",
        accentSoftColor: "#eaf3ff",
        zIndex: 2147483100,
      },
    },
    {
      name: "shop003-no-public-key",
      inputId: "gift-keyword",
      submitId: "gift-submit",
      targetId: "",
      keyword: "크리스탈 상패",
      updatedKeyword: "투명 감사패",
      expectedTitle: "AI 상품 검색",
      expectedAccent: "#7c3aed",
      expectedZIndex: 2147483200,
      expectedMallId: "shop003",
      options: {
        target: "",
        attachToSearchInput: "#gift-keyword",
        attachAfterSelector: "#gift-submit",
        mallId: "shop003",
        apiBaseUrl: "http://localhost:8000/",
        triggerTitle: "AI 상품 검색",
        accentColor: "#7c3aed",
        accentSoftColor: "#f5f3ff",
        zIndex: 2147483200,
      },
    },
    {
      name: "shop004-auto-detected-search",
      inputId: "auto-keyword",
      inputName: "q",
      inputType: "search",
      inputPlaceholder: "상품명 검색",
      submitId: "auto-submit",
      submitText: "검색",
      targetId: "",
      keyword: "무선 충전기",
      updatedKeyword: "고속 충전기",
      expectedTitle: "AI 이미지검색",
      expectedAccent: "#ee1b24",
      expectedZIndex: 2147483000,
      expectedMallId: "shop004",
      expectedInsertedAfter: "auto-submit",
      options: {
        mallId: "shop004",
        apiBaseUrl: "http://localhost:8000",
      },
    },
  ];

  const results = [];
  for (const scenario of scenarios) {
    results.push(await runScenario(scenario));
  }
  writeReport({
    ok: true,
    local_only: true,
    not_operational_readiness: true,
    widget_config: Object.assign(
      assertConflictingAliasRejected(),
      assertMallIdValidation(),
      assertApiBaseUrlValidation(),
      await assertScriptSrcApiBaseUrlFallback(),
      await assertScriptDataAttributeAutoInit(),
      await assertUnsafeProductUrlsNeutralized(),
      assertDeferredInitUntilDomReady(),
      assertRepeatedInitReplacesWidget(),
      assertCssSpecialIdSelectorFallback(),
      assertComplexCssSpecialIdSelectorFallback(),
      assertAmbiguousExplicitSelectorRejected(),
      assertDynamicAutoAttachAfterDomMutation(),
      assertAutoAttachSkipsHiddenAndDisabledSearchInputs(),
      assertFallbackFloatingMountsWithoutSearchForm(),
      assertFallbackFloatingCoversMissingExplicitTargetWithoutWait(),
      assertFallbackFloatingCoversMissingExplicitTargetAfterWait()
    ),
    checked_sites: results,
  }, cli.output);
}

main().catch((error) => {
  console.error(error.stack || String(error));
  process.exit(1);
});
